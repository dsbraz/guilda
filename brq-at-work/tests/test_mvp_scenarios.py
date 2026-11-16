"""Acceptance scenarios of the MVP (PRD v0.27, §MVP): the weekly project follow-up agent.

Each stays a strict expected failure until the slice that delivers it replaces it with the
real test, run with fakes for SharePoint, Granola and Teams.

Slice 11 proves the conversation and the correction up to the agent: the question is
posted in the project's thread, the team's answer is kept with its author and cycle and
wakes the cycle through the outbox, and a correction, in the thread or as the reason of a
rejection, becomes context for the next cycle with who made it. Writing the answer into
the report and redoing it belong to the agent's cycle (slice 13).

Slice 10 proves the context: sources are ingested once per version, deleted or revoked
ones are forgotten, an unreachable connector is flagged, and recall in the project's
scope never returns a restricted source.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from brq_at_work.composition import WEEKLY_REPORT, bootstrap
from brq_at_work.publish_agent import AGENT_FILE, publish
from db_testing import Database, create_test_database, drain
from pxos_kernel.channel import FakeChannel
from pxos_kernel.context import FakeContextStore
from pxos_kernel.conversation import (
    Inbound,
    conversation,
    post,
    receive,
    retain_message,
    retain_rejection,
)
from pxos_kernel.identity import NotAuthorized, session
from pxos_kernel.ingestion import FakeConnector, SourceDocument, sync
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Event, Evidence, create_pool, propose, review

AGENT = "weekly-report-agent"
PROJECT = "project:aurora"
BOARD = "board:restricted"
THREAD = "teams:projeto-alfa"
INSTRUCTIONS = "Guarde cada correção do time com quem a fez."


@pytest.fixture(scope="module")
def installation(registry: Registry) -> Database:
    """A BRQ@work installation whose weekly agent is published from the distro's file."""
    database = create_test_database("pxos_test_brq_context")
    bootstrap(database.owner_dsn, registry)

    async def published() -> None:
        draft = await publish(database.app_dsn, AGENT_FILE, author="bruno")
        pool = await create_pool(database.app_dsn)
        try:
            async with session(pool, "eva") as connection:
                await review(connection, registry, draft.id, "approved", digest=draft.digest)
        finally:
            await pool.close()

    asyncio.run(published())
    return database


@pytest.fixture
async def context_pool(installation: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(installation.app_dsn)
    yield pool
    await pool.close()


def source(name: str, access: str, version: str = "1") -> SourceDocument:
    return SourceDocument(
        source=f"sharepoint:aurora/{name}",
        version=version,
        text=f"{name}, versão {version}: o diagnóstico atrasou uma semana.",
        access=(access,),
    )


async def states(pool: asyncpg.Pool, connector: str) -> dict[str, str]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT source, state FROM pxos.source_states WHERE connector = $1", connector
        )
    return {row["source"]: row["state"] for row in rows}


def pending(slice_: str, scope: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(raises=NotImplementedError, strict=True, reason=f"{slice_}: {scope}")


async def test_update_without_duplication(context_pool: asyncpg.Pool) -> None:
    """New or changed sources update the context, without duplicating it."""
    sharepoint, store = FakeConnector("sharepoint-update"), FakeContextStore()
    status = source("status.docx", PROJECT)
    sharepoint.put(status)
    await sync(context_pool, store, sharepoint, agent=AGENT)

    sharepoint.put(source("status.docx", PROJECT, version="2"))
    await sync(context_pool, store, sharepoint, agent=AGENT)
    sharepoint.put(source("status.docx", PROJECT, version="2"))
    await sync(context_pool, store, sharepoint, agent=AGENT)

    recalled = await store.recall("atrasos do diagnóstico", tags=[PROJECT])
    assert [(r.source, r.text) for r in recalled] == [
        (status.source, "status.docx, versão 2: o diagnóstico atrasou uma semana.")
    ]
    assert [key for key, _ in store.retained] == [status.source, status.source]


class TextStore(FakeContextStore):
    """The reference store, also keeping the text retained under each key."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: dict[str, str] = {}

    async def retain(self, key: str, text: str, instructions: str, **options: Any) -> None:
        await super().retain(key, text, instructions, **options)
        self.texts[key] = text


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    await drain(pool, {})
    yield pool
    await pool.close()


def said(sender: str, text: str) -> Inbound:
    return Inbound(thread=THREAD, channel_message=f"m-{uuid4()}", sender=sender, text=text)


async def test_clarification(pool: asyncpg.Pool, publisher: asyncpg.Pool) -> None:
    """A gap becomes a question in the thread; the team's answer is kept for the cycle."""
    channel = FakeChannel()
    cycle = f"semana-{uuid4()}"

    question = await post(
        pool, channel, AGENT, thread=THREAD, cycle=cycle, text="A entrega de sexta saiu?"
    )
    woken: list[Event] = []

    async def wake(event: Event) -> None:
        woken.append(event)

    answer = await receive(pool, said("teams:bruno", "Saiu na segunda: faltou ambiente."))
    for stranger in ("teams:dora", "teams:convidado"):
        with pytest.raises(NotAuthorized):
            await receive(pool, said(stranger, "Saiu sim."))
    await drain(publisher, {"message.received": wake})

    assert channel.threads[THREAD][-1] == "A entrega de sexta saiu?"
    assert (answer.author, answer.cycle) == ("bruno", cycle)
    async with session(pool, AGENT) as connection:
        assert await conversation(connection, cycle) == [question, answer]
    assert [event.payload for event in woken] == [{"message_id": str(answer.id), "cycle": cycle}]


async def rejected(pool: asyncpg.Pool, registry: Registry, reason: str) -> UUID:
    """A weekly report proposed by the agent and rejected by Ana, the PM, with ``reason``."""
    async with session(pool, AGENT) as connection:
        proposal = await propose(
            connection,
            registry.get(WEEKLY_REPORT),
            "propose_weekly_report",
            payload={
                "summary": "Semana do projeto Alfa.",
                "items": [
                    {
                        "category": "confirmed_delay",
                        "text": "O marco 2 atrasou.",
                        "sources": ["ata-2026-11-10"],
                    }
                ],
            },
            evidence=Evidence(source="test:run", quote="Report", context_ref=f"t:{uuid4()}"),
            valid_from=datetime.now(UTC),
        )
    async with session(pool, "ana") as connection:
        await review(
            connection, registry, proposal.id, "rejected", digest=proposal.digest, reason=reason
        )
    return proposal.id


async def test_continuity(pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry) -> None:
    """This week's corrections are context for the next cycle, with who made them."""
    this_week = f"semana-{uuid4()}"
    await post(
        pool, FakeChannel(), AGENT, thread=THREAD, cycle=this_week, text="O marco 2 atrasou?"
    )
    correction = await receive(
        pool, said("teams:ana", "Não atrasou: o prazo foi renegociado para 30/11.")
    )
    proposal_id = await rejected(pool, registry, "Distinga atraso confirmado de renegociado.")
    store = TextStore()

    async def retain_answer(event: Event) -> None:
        await retain_message(
            pool, store, AGENT, UUID(event.payload["message_id"]), INSTRUCTIONS, tags=[PROJECT]
        )

    async def retain_reason(event: Event) -> None:
        await retain_rejection(
            pool, store, AGENT, UUID(event.payload["proposal_id"]), INSTRUCTIONS, tags=[PROJECT]
        )

    await drain(publisher, {"message.received": retain_answer, "proposal.rejected": retain_reason})

    answer_key, reason_key = f"message:{correction.id}", f"rejection:{proposal_id}"
    assert store.retained == [(answer_key, INSTRUCTIONS), (reason_key, INSTRUCTIONS)]
    assert store.texts[answer_key] == (
        f"Message from ana in cycle {this_week}: Não atrasou: o prazo foi renegociado para 30/11."
    )
    assert store.texts[reason_key] == (
        f"Correction from ana: proposal {proposal_id} (WeeklyReport) was rejected"
        " because: Distinga atraso confirmado de renegociado."
    )
    async with session(pool, AGENT) as connection:
        assert correction in await conversation(connection, this_week)


async def test_sources_and_access(context_pool: asyncpg.Pool) -> None:
    """Unavailability is flagged; deletion, lost access and restricted access hold."""
    sharepoint, store = FakeConnector("sharepoint-access"), FakeContextStore()
    status, board = source("andamento.docx", PROJECT), source("diretoria.docx", BOARD)
    deleted, revoked = source("rascunho.docx", PROJECT), source("contrato.docx", PROJECT)
    for document in (status, board, deleted, revoked):
        sharepoint.put(document)
    await sync(context_pool, store, sharepoint, agent=AGENT)

    sharepoint.delete(deleted.source)
    sharepoint.revoke(revoked.source)
    await sync(context_pool, store, sharepoint, agent=AGENT)

    # Restricted information does not reach an answer: recall in the project's scope
    # returns neither the board's source nor the deleted or revoked ones.
    recalled = await store.recall("atrasos do diagnóstico", tags=[PROJECT])
    assert {r.source for r in recalled} == {status.source}
    assert await states(context_pool, sharepoint.name) == {
        status.source: "available",
        board.source: "available",
        deleted.source: "deleted",
        revoked.source: "no_access",
    }

    # An unreachable SharePoint is flagged for the report, and nothing is removed.
    sharepoint.available = False
    assert await sync(context_pool, store, sharepoint, agent=AGENT) is False
    assert (await states(context_pool, sharepoint.name))[status.source] == "unavailable"
    assert await store.recall("atrasos do diagnóstico", tags=[PROJECT]) == recalled


@pending("fatia 13", "reinício durante a espera preserva respostas e não duplica entregas")
def test_resumption() -> None:
    raise NotImplementedError


@pending("fatia 11", "o PM revisa o status antes de compartilhar com o cliente")
def test_pm_reviews_the_deliverable() -> None:
    raise NotImplementedError


@pending("fatia 13", "cada execução registra uso e custo para medir o esforço total")
def test_pm_total_effort_is_measurable() -> None:
    raise NotImplementedError
