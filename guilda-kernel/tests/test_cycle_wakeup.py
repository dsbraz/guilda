"""The outbox wakes the agent's cycle and the version schedules it (ADR 0006 §1, §2).

The kernel's publisher composes, per event: a team's answer is retained in the version's
recall scope and wakes its cycle; a rejection's reason is retained the same way and wakes
the cycle with the decision, as does an approval; a new version of the agent creates or
updates the Temporal Schedule of its cycles. Events of anything else, and of cycles that
are over, pass through without stopping the outbox.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, RESTOCK_AGENT, installation
from temporalio.client import Client
from test_cycle_workflow import ASKS, REASON, asked, order, proposal, worker

from agents_testing import publish
from db_testing import Database, drain
from fakes_testing import FakeChannel, FakeContextStore
from guilda_kernel.agents import approved_version
from guilda_kernel.conversation import Inbound, receive
from guilda_kernel.identity import session
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Handler, create_pool, review
from guilda_kernel.workflows import cycle_handlers, schedule_id, start_cycle
from model_testing import ROUTE


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    yield pool
    await pool.close()


def handlers(
    pool: asyncpg.Pool, registry: Registry, store: FakeContextStore, temporal: Client, queue: str
) -> dict[str, Handler]:
    return cycle_handlers(
        pool, registry, store, FakeChannel(), temporal, agent=AGENT, task_queue=queue
    )


async def test_answers_and_reviews_are_retained_in_scope_and_wake_the_cycle(
    temporal: Client, pool: asyncpg.Pool, publisher: asyncpg.Pool, registry: Registry
) -> None:
    def reply(data: dict[str, Any]) -> str:
        return order() if data["answers"] else ASKS

    store = FakeContextStore()
    async with worker(temporal, pool, registry, reply) as (queue, _, _):
        wake = handlers(pool, registry, store, temporal, queue)
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        await asked(pool, handle.id)
        inbound = Inbound("thread:aisle-3", f"m-{uuid4()}", "chat:erin", "Aisle 3.")
        answer = await receive(pool, inbound)
        await drain(publisher, wake)
        first = await proposal(pool, handle.id)
        async with session(pool, "carla") as connection:
            await review(
                connection, registry, first.id, "rejected", digest=first.digest, reason=REASON
            )
        await drain(publisher, wake)
        # The restock agent's version allows two rounds: the rejection ends the cycle.
        assert await asyncio.wait_for(handle.result(), timeout=30) == "rejected"

    async with worker(temporal, pool, registry, lambda data: order()) as (queue, _, _):
        wake = handlers(pool, registry, store, temporal, queue)
        handle = await start_cycle(temporal, AGENT, f"week-{uuid4()}", task_queue=queue)
        approved = await proposal(pool, handle.id)
        async with session(pool, "carla") as connection:
            await review(connection, registry, approved.id, "approved", digest=approved.digest)
        await drain(publisher, wake)
        assert await asyncio.wait_for(handle.result(), timeout=30) == "approved"

        late = Inbound("thread:aisle-3", f"m-{uuid4()}", "chat:erin", "Also aisle 4.")
        await receive(pool, late)  # joins a cycle that is over
        await drain(publisher, wake)

    scope = ("store:aisle-3",)  # the restock agent's recall tags
    found = {r.source for r in await store.recall("aisle", tags=scope)}
    assert {f"message:{answer.id}", f"rejection:{first.id}"} <= found
    assert dict(store.retained)[f"message:{answer.id}"] == RESTOCK_AGENT["memory_instructions"]


@pytest.fixture
def scheduled(registry: Registry) -> Database:
    return installation("guilda_test_cycle_schedule", registry, ROUTE)


async def test_each_version_of_the_agent_schedules_its_cycles(
    temporal: Client, scheduled: Database, registry: Registry
) -> None:
    pool = await create_pool(scheduled.app_dsn)
    publisher = await create_pool(scheduled.publisher_dsn)
    queue = f"cycle-{uuid4()}"
    agent = AGENT
    try:
        wake = handlers(pool, registry, FakeContextStore(), temporal, queue)
        await publish(pool, registry, RESTOCK_AGENT, author="gina", approver="paula")
        await drain(publisher, wake)
        first = await temporal.get_schedule_handle(schedule_id(agent)).describe()

        async with session(pool, AGENT) as connection:
            version = await approved_version(connection, AGENT)
        friday = {**RESTOCK_AGENT, "schedule": "30 14 * * 5"}
        await publish(pool, registry, friday, author="gina", approver="paula", change=version)
        await drain(publisher, wake)
        second = await temporal.get_schedule_handle(schedule_id(agent)).describe()
    finally:
        await temporal.get_schedule_handle(schedule_id(agent)).delete()
        await pool.close()
        await publisher.close()

    def when(spec: Any) -> tuple[int, int, int]:
        (calendar,) = spec.calendars
        return calendar.minute[0].start, calendar.hour[0].start, calendar.day_of_week[0].start

    assert when(first.schedule.spec) == (0, 9, 1)
    assert when(second.schedule.spec) == (30, 14, 5)
    action = second.schedule.action
    assert (action.workflow, action.task_queue) == ("AgentCycle", queue)  # type: ignore[attr-defined]
    assert await temporal.data_converter.decode(action.args) == [AGENT]  # type: ignore[attr-defined]
