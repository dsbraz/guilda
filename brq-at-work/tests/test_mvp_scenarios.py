"""Acceptance scenarios of the MVP (PRD v0.27, §MVP): the weekly project follow-up agent,
run by the kernel's cycle with fakes for SharePoint, Granola, Teams and the model, on the
Temporal development server.

The context: sources are ingested once per version, deleted or revoked ones are
forgotten, an unreachable connector is flagged, and recall in the project's scope never
returns a restricted source.

The cycle: a gap becomes a question in the project's thread and the team's answer goes
into the report with ``message:<id>`` as its source; the PM reviews the card, with its
sources and uncertainties, rejects with a reason and approves what the agent redid, and
nothing reaches the client; the reason is retained in the version's recall scope, so the
next cycle does not repeat the error; a worker killed while the cycle waits for an answer
or a review loses nothing and delivers once; and each cycle's effort is readable.
"""

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from temporalio.client import Client, WorkflowHandle

from brq_at_work.composition import ACCESS, bootstrap
from brq_at_work.publish_agent import AGENT_FILE, publish
from brq_at_work.serve import AGENT
from db_testing import Database, create_test_database, drain
from fakes_testing import FakeChannel, FakeConnector, FakeContextStore
from guilda_kernel.agents import cycle_effort, round_input
from guilda_kernel.channel import Card, decide
from guilda_kernel.conversation import Inbound, Message, conversation, receive
from guilda_kernel.identity import NotAuthorized, session
from guilda_kernel.ingestion import SourceDocument, sync
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Decision, Proposal, create_pool, review
from guilda_kernel.workflows import cycle_handlers, start_cycle
from model_testing import ROUTE

from .pilot import QUESTION, TOKENS, Script, continuing, cycle_worker, reviewing, weekly

PROJECT = "project:aurora"
BOARD = "board:restricted"
THREAD = "projeto-piloto"  # the version's thread
PILOT = Path(__file__).with_name("pilot.py")


@pytest.fixture(scope="module")
def installation(registry: Registry) -> Database:
    """A BRQ@work installation whose weekly agent is published from the distro's file."""
    database = create_test_database("guilda_test_brq_context")
    bootstrap(database.owner_dsn, registry, route=ROUTE)

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


@pytest.fixture
async def publisher(installation: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(installation.publisher_dsn)
    await drain(pool, {})
    yield pool
    await pool.close()


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


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
            "SELECT source, state FROM guilda.source_states WHERE connector = $1", connector
        )
    return {row["source"]: row["state"] for row in rows}


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


@dataclass
class Pilot:
    """The kernel's cycle of the weekly agent, and the outbox publisher that wakes it."""

    temporal: Client
    pool: asyncpg.Pool
    publisher: asyncpg.Pool
    registry: Registry
    queue: str = field(default_factory=lambda: f"pilot-{uuid4()}")
    store: FakeContextStore = field(default_factory=FakeContextStore)
    channel: FakeChannel = field(default_factory=FakeChannel)

    async def start(self) -> WorkflowHandle[Any, str]:
        return await start_cycle(self.temporal, AGENT, f"semana-{uuid4()}", task_queue=self.queue)

    async def wake(self) -> None:
        """Publish the outbox: cards to the PM, answers and reviews to the cycle."""
        handlers = cycle_handlers(
            self.pool,
            self.registry,
            self.store,
            self.channel,
            self.temporal,
            agent=AGENT,
            task_queue=self.queue,
        )
        await drain(self.publisher, handlers)

    async def card(self, proposal: Proposal) -> Card:
        await self.wake()
        return self.channel.cards["ana"][proposal.id]

    async def decide(
        self, proposal: Proposal, decision: Decision, reason: str | None = None
    ) -> None:
        """Ana, the PM, decides on the card she got, through the channel."""
        card = await self.card(proposal)
        await decide(self.pool, self.registry, "teams:ana", card, decision, reason=reason)
        await self.wake()


@asynccontextmanager
async def piloting(
    temporal: Client,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    script: Script,
) -> AsyncIterator[Pilot]:
    pilot = Pilot(temporal, pool, publisher, registry)
    async with cycle_worker(
        temporal,
        pool,
        registry,
        script,
        queue=pilot.queue,
        store=pilot.store,
        channel=pilot.channel,
    ):
        yield pilot


async def until[T](probe: Callable[[], Awaitable[T | None]], timeout: float = 30) -> T:
    async with asyncio.timeout(timeout):
        while (found := await probe()) is None:
            await asyncio.sleep(0.2)
        return found


async def proposals(pool: asyncpg.Pool, cycle: str) -> list[Proposal]:
    async with session(pool, AGENT) as connection:
        rows = await connection.fetch(
            "SELECT p.* FROM guilda.proposals p JOIN guilda.agent_runs a"
            " ON p.evidence->>'source' = 'agent-run:' || a.id WHERE a.cycle = $1"
            " ORDER BY p.created_at",
            cycle,
        )
    return [Proposal(**dict(row)) for row in rows]


async def proposed(pool: asyncpg.Pool, cycle: str, number: int = 1) -> Proposal:
    async def probe() -> Proposal | None:
        found = await proposals(pool, cycle)
        return found[number - 1] if len(found) >= number else None

    return await until(probe)


async def asked(pool: asyncpg.Pool, cycle: str) -> Message:
    """The agent's post of questions in ``cycle``, once it is kept."""

    async def probe() -> Message | None:
        async with session(pool, AGENT) as connection:
            posts = [m for m in await conversation(connection, cycle) if m.sender is None]
        return posts[0] if posts else None

    return await until(probe)


def said(sender: str, text: str) -> Inbound:
    return Inbound(thread=THREAD, channel_message=f"m-{uuid4()}", sender=sender, text=text)


async def outcome(handle: WorkflowHandle[Any, str]) -> str:
    return await asyncio.wait_for(handle.result(), timeout=60)


async def test_clarification(
    temporal: Client, context_pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    """A gap becomes a question in the thread; the answer goes into the report, cited."""
    async with piloting(temporal, context_pool, publisher, registry, weekly) as pilot:
        handle = await pilot.start()
        question = await asked(context_pool, handle.id)
        answer = await receive(context_pool, said("teams:bruno", "Saiu na segunda: sem ambiente."))
        for stranger in ("teams:dora", "teams:convidado"):
            with pytest.raises(NotAuthorized):
                await receive(context_pool, said(stranger, "Saiu sim."))
        await pilot.wake()
        report = await proposed(context_pool, handle.id)
        await pilot.decide(report, "approved")
        assert await outcome(handle) == "approved"

    assert pilot.channel.threads[THREAD] == [QUESTION]
    assert (question.text, answer.author, answer.cycle) == (QUESTION, "bruno", handle.id)
    (item,) = report.payload["items"]
    assert item["sources"] == [f"message:{answer.id}"]
    assert item["text"] == "Entrega de sexta: Saiu na segunda: sem ambiente."


async def test_continuity(
    temporal: Client, context_pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    """This week's correction holds in the next cycle, which does not repeat the error."""
    reason = "Não atrasou: o prazo foi renegociado para 30/11."
    async with piloting(temporal, context_pool, publisher, registry, continuing) as pilot:
        this_week = await pilot.start()
        wrong = await proposed(context_pool, this_week.id)
        await pilot.decide(wrong, "rejected", reason)
        fixed = await proposed(context_pool, this_week.id, 2)
        await pilot.decide(fixed, "approved")
        assert await outcome(this_week) == "approved"

        next_week = await pilot.start()
        report = await proposed(context_pool, next_week.id)
        await pilot.decide(report, "approved")
        assert await outcome(next_week) == "approved"

    (delayed,) = wrong.payload["items"]
    assert delayed["category"] == "confirmed_delay"
    assert [s.split("/")[1] for s in delayed["sources"]] == ["ata-2026-11-10"]  # no board note
    correction = f"rejection:{wrong.id}"
    renegotiated = {
        "category": "renegotiated_deadline",
        "text": "O prazo do marco 2 foi renegociado para 30/11.",
        "sources": [correction],
    }
    assert fixed.payload["items"] == report.payload["items"] == [renegotiated]
    # Next week's round brought no correction of its own: it came by recall, in scope.
    next_round = await round_input(context_pool, AGENT, next_week.id, final_round=False)
    assert next_round.corrections == ()
    recalled = await pilot.store.recall("marco 2", tags=list(ACCESS))
    assert correction in {r.source for r in recalled}


async def test_pm_reviews_the_deliverable(
    temporal: Client, context_pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    """The PM reviews the report, with sources and uncertainties, before the client."""
    async with piloting(temporal, context_pool, publisher, registry, reviewing) as pilot:
        handle = await pilot.start()
        risk = await proposed(context_pool, handle.id)
        first = await pilot.card(risk)
        await pilot.decide(risk, "rejected", "A data já passou: é atraso confirmado.")
        delay = await proposed(context_pool, handle.id, 2)
        second = await pilot.card(delay)
        await pilot.decide(delay, "approved")
        assert await outcome(handle) == "approved"

    (item,) = first.payload["items"]
    assert (item["category"], first.payload["uncertainties"]) == (
        "delay_risk",
        ["O cliente ainda não confirmou o novo escopo."],
    )
    assert item["sources"] and first.source.startswith("agent-run:")
    (item,) = second.payload["items"]
    assert item["category"] == "confirmed_delay"
    assert item["sources"][-1] == f"rejection:{risk.id}"
    # Nothing goes to the client: Dora gets no card and no post.
    assert set(pilot.channel.cards) == {"ana"}
    assert dict(pilot.channel.threads) == {}


async def test_pm_total_effort_is_measurable(
    temporal: Client, context_pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    """Each cycle's rounds, questions, answers, rejections, review time and cost read back."""
    async with piloting(temporal, context_pool, publisher, registry, weekly) as pilot:
        handle = await pilot.start()
        await asked(context_pool, handle.id)
        await receive(context_pool, said("teams:bruno", "Saiu, com um dia de atraso."))
        await pilot.wake()
        await pilot.decide(await proposed(context_pool, handle.id), "rejected", "Cite a ata.")
        await pilot.decide(await proposed(context_pool, handle.id, 2), "approved")
        assert await outcome(handle) == "approved"

    async with session(context_pool, AGENT) as connection:
        effort = await cycle_effort(connection, handle.id)
    calls = 3 * 2  # three rounds, each a recall and an answer
    input_tokens, output_tokens = calls * TOKENS[0], calls * TOKENS[1]
    assert (effort.rounds, effort.questions, effort.answers, effort.rejections) == (3, 1, 1, 1)
    assert effort.review_time > timedelta(0)
    assert (effort.input_tokens, effort.output_tokens) == (input_tokens, output_tokens)
    cost = input_tokens * ROUTE.input_price + output_tokens * ROUTE.output_price
    assert effort.cost == cost / 1_000_000


@asynccontextmanager
async def worker_process(dsn: str, address: str, queue: str) -> AsyncIterator[None]:
    """A worker of the cycle in its own process, killed with SIGKILL on the way out. The
    repository root is on its path, for the test helpers the pilot imports."""
    process = subprocess.Popen(
        [sys.executable, str(PILOT), dsn, address, queue],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(PILOT.parents[2])},
    )
    try:
        assert process.stdout is not None
        assert (await asyncio.to_thread(process.stdout.readline)).strip() == "ready"
        yield
    finally:
        os.kill(process.pid, signal.SIGKILL)
        await asyncio.to_thread(process.wait)


async def settled(handle: WorkflowHandle[Any, str], activities: int) -> None:
    """Wait until ``activities`` steps completed and the cycle waits, nothing running."""

    async def probe() -> bool | None:
        events = (await handle.fetch_history()).events
        done = sum(e.HasField("activity_task_completed_event_attributes") for e in events)
        running = (await handle.describe()).raw_description
        idle = not running.pending_activities and not running.HasField("pending_workflow_task")
        return True if done == activities and idle else None

    await until(probe)


async def test_resumption(
    temporal: Client,
    temporal_address: str,
    installation: Database,
    context_pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
) -> None:
    """Killing the worker while the cycle waits keeps what arrived and delivers once."""
    pilot = Pilot(temporal, context_pool, publisher, registry)
    run = (installation.app_dsn, temporal_address, pilot.queue)

    async with worker_process(*run):
        handle = await pilot.start()
        question = await asked(context_pool, handle.id)
        await settled(handle, 4)  # pinned, synced, ran round 1, posted: waits for an answer
    answer = await receive(context_pool, said("teams:bruno", "Saiu na terça."))
    await pilot.wake()  # the answer arrives while no worker runs

    async with worker_process(*run):
        report = await proposed(context_pool, handle.id)
        await settled(handle, 6)  # ran round 2, proposed: waits for the review
    await pilot.decide(report, "approved")  # the PM decides while no worker runs

    async with worker_process(*run):
        assert await outcome(handle) == "approved"

    assert await proposals(context_pool, handle.id) == [report]
    assert report.payload["items"][0]["sources"] == [f"message:{answer.id}"]
    async with session(context_pool, AGENT) as connection:
        assert await conversation(connection, handle.id) == [question, answer]


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
