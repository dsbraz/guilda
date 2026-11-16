"""The agent's cycle is one kernel workflow, set by the published version (ADR 0006 §2).

It pins the version in force, syncs the connectors and runs rounds: a round's questions
go to the thread and the cycle waits for an answer until the version's deadline; a
deliverable is proposed and the cycle waits for its review. Approved ends the cycle,
rejected runs another round with the reason as a correction, and the version's maximum
of rounds ends it. Signals only wake the cycle; the questions, the answers, the
deliverable and the reason stay out of the Temporal history.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT
from google.adk.models.llm_request import LlmRequest
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from pxos_kernel.channel import FakeChannel
from pxos_kernel.context import FakeContextStore
from pxos_kernel.conversation import Inbound, receive
from pxos_kernel.identity import session
from pxos_kernel.ingestion import FakeConnector, SourceDocument
from pxos_kernel.model_access import FakeModels, ModelGateway
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Proposal, review
from pxos_kernel.workflows import (
    AgentCycle,
    CycleActivities,
    cycle_workflow_id,
    start_cycle,
    workflow_runner,
)
from pxos_kernel.workflows.definitions import Pinned
from temporal_testing import history_bytes

QUESTION, ANSWER, SKU, REASON = (f"secret-{name}-{uuid4()}" for name in "qasr")
Reply = Callable[[dict[str, Any]], str]


def order(quantity: int = 12) -> str:
    deliverable = {"sku": SKU, "quantity": quantity, "occurred_at": "2026-11-14T09:00:00Z"}
    return json.dumps({"questions": None, "deliverable": deliverable})


ASKS = json.dumps({"questions": [QUESTION], "deliverable": None})


def scripted(reply: Reply) -> FakeModels:
    """A model whose answer is a function of the round's data, as the agent got it."""

    def answer(request: LlmRequest) -> str:
        text = str(request.contents[0].parts[0].text)  # type: ignore[index]
        return reply(json.loads(text))

    return FakeModels(reply=answer, tokens=(10, 2))


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


class ShortDeadline(CycleActivities):
    """Answers are due a second after the questions, so the deadline passes in a test."""

    async def pin(self, agent: str) -> Pinned:
        pinned = await super().pin(agent)
        return Pinned(pinned.entity_id, pinned.revision, pinned.max_rounds, 1.0)


@asynccontextmanager
async def worker(
    temporal: Client,
    pool: asyncpg.Pool,
    registry: Registry,
    reply: Reply,
    activities: type[CycleActivities] = CycleActivities,
) -> AsyncIterator[tuple[str, FakeChannel, FakeConnector]]:
    queue, channel = f"cycle-{uuid4()}", FakeChannel()
    connector = FakeConnector(f"fixture-{uuid4()}")
    connector.put(
        SourceDocument(source=f"fixture:{uuid4()}", version="1", text="A-1.", access=("x",))
    )
    steps = activities(
        pool,
        FakeContextStore(),
        [connector],
        ModelGateway(pool, scripted(reply)),
        registry,
        channel,
    )
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[AgentCycle],
        activities=steps.registered(),
        workflow_runner=workflow_runner(),
    ):
        yield queue, channel, connector


async def until[T](probe: Callable[[], Awaitable[T | None]]) -> T:
    async with asyncio.timeout(30):
        while (found := await probe()) is None:
            await asyncio.sleep(0.2)
        return found


async def proposals(pool: asyncpg.Pool, cycle: str) -> list[Proposal]:
    async with session(pool, AGENT) as connection:
        rows = await connection.fetch(
            "SELECT p.* FROM pxos.proposals p JOIN pxos.agent_runs a"
            " ON p.evidence->>'source' = 'agent-run:' || a.id WHERE a.cycle = $1"
            " ORDER BY p.created_at",
            cycle,
        )
    return [Proposal(**dict(row)) for row in rows]


async def proposal(pool: asyncpg.Pool, cycle: str, number: int = 1) -> Proposal:
    async def probe() -> Proposal | None:
        found = await proposals(pool, cycle)
        return found[number - 1] if len(found) >= number else None

    return await until(probe)


async def decide(
    pool: asyncpg.Pool,
    registry: Registry,
    handle: WorkflowHandle[Any, Any],
    proposed: Proposal,
    decision: str,
) -> None:
    async with session(pool, "carla") as connection:
        await review(
            connection,
            registry,
            proposed.id,
            decision,  # type: ignore[arg-type]
            digest=proposed.digest,
            reason=REASON if decision == "rejected" else None,
        )
    await handle.signal(AgentCycle.reviewed, args=[str(proposed.id), decision])


async def asked(channel: FakeChannel, thread: str) -> None:
    async def probe() -> bool | None:
        return True if channel.threads[thread] else None

    await until(probe)


async def test_the_cycle_asks_waits_for_the_answer_and_delivers_it(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    async with worker(
        temporal, pool, registry, lambda data: order() if data["answers"] else ASKS
    ) as (queue, channel, connector):
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        await asked(channel, "thread:aisle-3")
        inbound = Inbound("thread:aisle-3", f"m-{uuid4()}", "chat:erin", ANSWER)
        answer = await receive(pool, inbound)
        await handle.signal(AgentCycle.answered)
        delivered = await proposal(pool, handle.id)
        await decide(pool, registry, handle, delivered, "approved")
        assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"

    assert channel.threads["thread:aisle-3"][-1] == QUESTION
    assert answer.cycle == handle.id
    assert delivered.payload["sku"] == SKU
    async with session(pool, AGENT) as connection:
        cursor = await connection.fetchval(
            "SELECT cursor FROM pxos.connector_cursors WHERE connector = $1", connector.name
        )
    assert cursor == "1"  # the cycle synced its connectors
    raw = await history_bytes(temporal, handle.id)
    assert handle.id.encode() in raw  # the search sees what history holds
    for secret in (QUESTION, ANSWER, SKU):
        assert secret.encode() not in raw


async def test_a_rejection_is_a_correction_for_another_round(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    def reply(data: dict[str, Any]) -> str:
        return order(10) if any(REASON in c for c in data["corrections"]) else order(12)

    async with worker(temporal, pool, registry, reply) as (queue, _, _):
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        await decide(pool, registry, handle, await proposal(pool, handle.id), "rejected")
        redone = await proposal(pool, handle.id, 2)
        await decide(pool, registry, handle, redone, "approved")
        assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"

    assert [p.payload["quantity"] for p in await proposals(pool, handle.id)] == [12, 10]
    assert REASON.encode() not in await history_bytes(temporal, handle.id)


async def test_past_the_deadline_the_last_round_delivers_and_the_rounds_end(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    """The restock agent's version allows two rounds."""

    def reply(data: dict[str, Any]) -> str:
        return order() if data["final_round"] else ASKS

    async with worker(temporal, pool, registry, reply, ShortDeadline) as (queue, _, _):
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        await decide(pool, registry, handle, await proposal(pool, handle.id), "rejected")
        assert await asyncio.wait_for(handle.result(), timeout=30) == "rejected"

    assert len(await proposals(pool, handle.id)) == 1


async def test_the_same_cycle_never_runs_twice(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    async with worker(temporal, pool, registry, lambda data: order()) as (queue, _, _):
        cycle = f"week-{uuid4()}"
        handle = await start_cycle(temporal, AGENT, cycle, task_queue=queue)
        assert handle.id == cycle_workflow_id(AGENT, cycle)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start_cycle(temporal, AGENT, cycle, task_queue=queue)
        await decide(pool, registry, handle, await proposal(pool, handle.id), "approved")
        await asyncio.wait_for(handle.result(), timeout=30)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start_cycle(temporal, AGENT, cycle, task_queue=queue)


async def test_a_failed_round_reaches_history_by_its_type_alone(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    async with worker(temporal, pool, registry, lambda data: f"not json {SKU}") as (queue, _, _):
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)

    raw = await history_bytes(temporal, handle.id)
    assert b"run_round failed: InvalidOutput" in raw
    assert SKU.encode() not in raw
