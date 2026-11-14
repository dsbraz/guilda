"""A workflow waits for the decision on a proposal (PXOS-69, ADR 0004 §2).

The kernel is the authority: the workflow reads the decision through an Activity in the
agent's database session. The outbox publisher only nudges it with a signal, and a
proposal nobody waits for must not block the outbox.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from db_testing import Database
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import (
    Event,
    Evidence,
    Proposal,
    create_pool,
    get_review,
    propose,
    publish,
    review,
)
from pxos_kernel.workflows import (
    AwaitReview,
    ReviewActivities,
    definitions,
    review_signals,
    review_workflow_id,
    workflow_runner,
)


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@pytest.fixture
async def worker(temporal: Client, pool: asyncpg.Pool) -> AsyncIterator[str]:
    queue = f"review-{uuid4()}"
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[AwaitReview],
        activities=ReviewActivities(pool, actor="erin").registered(),
        workflow_runner=workflow_runner(),
    ):
        yield queue


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    yield pool
    await pool.close()


async def proposed(pool: asyncpg.Pool, registry: Registry) -> Proposal:
    async with session(pool, "erin") as connection:
        return await propose(
            connection,
            registry.get("retail"),
            "create_restock_order",
            payload={"sku": "W-1", "quantity": 1, "occurred_at": "2026-11-14T09:00:00Z"},
            evidence=Evidence(source="fixture:wf", quote="W-1", context_ref="fixture:wf"),
            valid_from=datetime(2026, 11, 14, tzinfo=UTC),
        )


async def decided(
    pool: asyncpg.Pool, registry: Registry, proposal: Proposal, decision: str
) -> None:
    async with session(pool, "carla") as connection:
        await review(
            connection,
            registry,
            proposal.id,
            decision,  # type: ignore[arg-type]
            digest=proposal.digest,
            reason="não precisa" if decision == "rejected" else None,
        )


async def start(temporal: Client, queue: str, proposal_id: UUID):  # type: ignore[no-untyped-def]
    return await temporal.start_workflow(
        AwaitReview.run,
        str(proposal_id),
        id=review_workflow_id(proposal_id),
        task_queue=queue,
    )


async def first_read_done(handle: WorkflowHandle[AwaitReview, str]) -> None:
    for _ in range(300):
        history = await handle.fetch_history()
        if any(e.HasField("activity_task_completed_event_attributes") for e in history.events):
            return
        await asyncio.sleep(0.1)
    raise AssertionError("the workflow never read the decision")


async def test_get_review_reads_the_decision_once_there_is_one(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    proposal = await proposed(pool, registry)
    async with session(pool, "erin") as connection:
        assert await get_review(connection, proposal.id) is None
    await decided(pool, registry, proposal, "approved")
    async with session(pool, "erin") as connection:
        found = await get_review(connection, proposal.id)
    assert found is not None and found.decision == "approved"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_the_decision_reaches_the_waiting_workflow(
    temporal: Client,
    worker: str,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    decision: str,
) -> None:
    signals = review_signals(temporal)
    await publish(publisher, signals)  # events left by earlier tests are not ours
    proposal = await proposed(pool, registry)
    handle = await start(temporal, worker, proposal.id)
    await decided(pool, registry, proposal, decision)
    await publish(publisher, signals)
    assert await asyncio.wait_for(handle.result(), timeout=30) == decision


async def test_a_signal_is_not_a_decision(
    temporal: Client, worker: str, pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    proposal = await proposed(pool, registry)
    handle = await start(temporal, worker, proposal.id)
    await handle.signal(AwaitReview.reviewed)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(handle.result()), timeout=2)

    await decided(pool, registry, proposal, "approved")
    await publish(publisher, review_signals(temporal))
    assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"


async def test_a_decision_taken_before_anyone_waits_is_seen(
    temporal: Client, worker: str, pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    proposal = await proposed(pool, registry)
    await decided(pool, registry, proposal, "approved")
    await publish(publisher, review_signals(temporal))  # nobody waits yet: ignored
    handle = await start(temporal, worker, proposal.id)
    assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"


async def test_a_lost_signal_costs_only_the_recheck(
    temporal: Client,
    worker: str,
    pool: asyncpg.Pool,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(definitions, "RECHECK", timedelta(seconds=1))
    proposal = await proposed(pool, registry)
    handle = await start(temporal, worker, proposal.id)
    await first_read_done(handle)  # the workflow saw no decision and waits
    await decided(pool, registry, proposal, "approved")  # and the outbox never runs
    assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"


async def test_failures_do_not_hide_behind_the_wait(temporal: Client, worker: str) -> None:
    handle = await start(temporal, worker, uuid4())
    with pytest.raises(WorkflowFailureError):
        await asyncio.wait_for(handle.result(), timeout=30)


async def test_a_signal_that_fails_for_another_reason_keeps_the_event() -> None:
    """Only "nobody waits" is ignored; an unavailable server fails the handler, so the
    outbox keeps the event for the next run."""

    class Unavailable:
        async def signal(self, *args: object) -> None:
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")

    class Server:
        def get_workflow_handle(self, workflow_id: str) -> Unavailable:
            return Unavailable()

    nudge = review_signals(Server())["fact.recorded"]  # type: ignore[arg-type]
    event = Event(1, "fact.recorded", uuid4(), {"proposal_id": str(uuid4())}, datetime.now(UTC))
    with pytest.raises(RPCError):
        await nudge(event)
