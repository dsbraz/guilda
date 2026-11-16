"""Acceptance scenarios of the MVP (PRD v0.27, §MVP): the weekly project follow-up agent.

Each stays a strict expected failure until the slice that delivers it replaces it with the
real test, run with fakes for SharePoint, Granola and Teams.

Slice 11 proves the conversation and the correction up to the agent: the question is
posted in the project's thread, the team's answer is kept with its author and cycle and
wakes the cycle through the outbox, and a correction, in the thread or as the reason of a
rejection, becomes context for the next cycle with who made it. Writing the answer into
the report and redoing it belong to the agent's cycle (slice 13).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from brq_at_work.composition import WEEKLY_REPORT
from db_testing import Database, drain
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
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Event, Evidence, create_pool, propose, review

AGENT = "weekly-report-agent"
THREAD = "teams:projeto-alfa"
INSTRUCTIONS = "Guarde cada correção do time com quem a fez."


def pending(slice_: str, scope: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(raises=NotImplementedError, strict=True, reason=f"{slice_}: {scope}")


@pending("fatia 10", "fontes novas ou alteradas atualizam o contexto, sem duplicar")
def test_update_without_duplication() -> None:
    raise NotImplementedError


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
        await retain_message(pool, store, AGENT, UUID(event.payload["message_id"]), INSTRUCTIONS)

    async def retain_reason(event: Event) -> None:
        await retain_rejection(pool, store, AGENT, UUID(event.payload["proposal_id"]), INSTRUCTIONS)

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


@pending("fatia 10", "indisponibilidade sinalizada; exclusão e perda de acesso respeitadas")
def test_sources_and_access() -> None:
    raise NotImplementedError


@pending("fatia 13", "reinício durante a espera preserva respostas e não duplica entregas")
def test_resumption() -> None:
    raise NotImplementedError


@pending("fatia 11", "o PM revisa o status antes de compartilhar com o cliente")
def test_pm_reviews_the_deliverable() -> None:
    raise NotImplementedError


@pending("fatia 13", "cada execução registra uso e custo para medir o esforço total")
def test_pm_total_effort_is_measurable() -> None:
    raise NotImplementedError
