"""A sync pass runs as a workflow that carries references only (K70, ADR 0004 §2).

The workflow receives a connector's name and an id derived from it. The Activity runs one
sync pass of that connector as the agent; what it hands back to the workflow is a flag. A
retry after the commit retains nothing twice, and nothing the source says reaches the
Temporal history, even when the connector or the context memory fails quoting it.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from fakes_testing import FakeConnector, FakeContextStore
from guilda_kernel.context import ContextUnavailable
from guilda_kernel.ingestion import Changes, Connector, SourceDocument
from guilda_kernel.workflows import (
    SyncActivities,
    SyncConnector,
    sync_workflow_id,
    workflow_runner,
)
from temporal_testing import history_bytes

SECRET = "restock aisle 3 with 12 boxes"
LOG = f"Erin: I will {SECRET} of A-1 tomorrow.\nGina: I will restock aisle 9 with 4 of B-2.\n"


def connector() -> FakeConnector:
    fake = FakeConnector(f"fixture-{uuid4()}")
    fake.put(
        SourceDocument(
            source=f"fixture:shelf-log/{uuid4()}", version="1", text=LOG, access=("access:x",)
        )
    )
    return fake


class Store(FakeContextStore):
    """Fails the first ``failures`` retains, quoting the text as a careless client would."""

    def __init__(self, failures: int = 0, error: type[Exception] = ConnectionResetError) -> None:
        super().__init__()
        self.failures = failures
        self.error = error

    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        if self.failures:
            self.failures -= 1
            raise self.error(f"cannot retain {text}")
        await super().retain(key, text, instructions, tags=tags)


class Garbled(FakeConnector):
    """A connector that fails quoting the source it could not read."""

    async def changes(self, cursor: str | None) -> Changes:
        raise ValueError(f"cannot parse {LOG}")


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@asynccontextmanager
async def worker(
    temporal: Client,
    pool: asyncpg.Pool,
    fake: Connector,
    store: FakeContextStore,
    activities: type[SyncActivities] = SyncActivities,
) -> AsyncIterator[str]:
    queue = f"sync-{uuid4()}"
    syncing = activities(pool, store, [fake], agent=AGENT)
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[SyncConnector],
        activities=syncing.registered(),
        workflow_runner=workflow_runner(),
    ):
        yield queue


async def start(temporal: Client, queue: str, name: str) -> WorkflowHandle[SyncConnector, bool]:
    return await temporal.start_workflow(
        SyncConnector.run, name, id=sync_workflow_id(name), task_queue=queue
    )


async def test_the_workflow_syncs_a_connector_by_reference(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    fake, store = connector(), Store()
    async with worker(temporal, pool, fake, store) as queue:
        handle = await start(temporal, queue, fake.name)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start(temporal, queue, fake.name)
        assert await asyncio.wait_for(handle.result(), timeout=30) is True

    assert len(store.retained) == 1
    raw = await history_bytes(temporal, handle.id)
    assert fake.name.encode() in raw  # the search sees what history holds
    assert SECRET.encode() not in raw


class CrashesAfterCommit(SyncActivities):
    """The first attempt commits, then dies before the workflow hears of it."""

    async def sync(self, connector: Connector) -> bool:
        synced = await super().sync(connector)
        if activity.info().attempt == 1:
            raise ConnectionResetError("worker lost after commit")
        return synced


async def test_a_retry_after_the_commit_retains_nothing_twice(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    fake, store = connector(), Store()
    async with worker(temporal, pool, fake, store, CrashesAfterCommit) as queue:
        handle = await start(temporal, queue, fake.name)
        assert await asyncio.wait_for(handle.result(), timeout=30) is True

    assert len(store.retained) == 1


@pytest.mark.parametrize("error", [ConnectionResetError, ContextUnavailable])
async def test_a_transient_failure_is_retried(
    temporal: Client, pool: asyncpg.Pool, error: type[Exception]
) -> None:
    fake, store = connector(), Store(failures=1, error=error)
    async with worker(temporal, pool, fake, store) as queue:
        handle = await start(temporal, queue, fake.name)
        assert await asyncio.wait_for(handle.result(), timeout=30) is True

    assert len(store.retained) == 1


@pytest.mark.parametrize(
    ("make_connector", "make_store"),
    [
        pytest.param(connector, lambda: Store(failures=1, error=ValueError), id="context memory"),
        pytest.param(lambda: Garbled("fixture-garbled"), Store, id="connector"),
    ],
)
async def test_a_failure_does_not_carry_the_source_into_history(
    temporal: Client,
    pool: asyncpg.Pool,
    make_connector: Callable[[], Connector],
    make_store: Callable[[], Store],
) -> None:
    fake, store = make_connector(), make_store()
    async with worker(temporal, pool, fake, store) as queue:
        handle = await start(temporal, queue, fake.name)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)

    assert SECRET.encode() not in await history_bytes(temporal, handle.id)


async def test_an_unreachable_connector_ends_the_pass_unsynced(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    fake = connector()
    fake.available = False
    async with worker(temporal, pool, fake, Store()) as queue:
        handle = await start(temporal, queue, fake.name)
        assert await asyncio.wait_for(handle.result(), timeout=30) is False


async def test_an_unknown_connector_fails_the_workflow(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    async with worker(temporal, pool, connector(), Store()) as queue:
        handle = await start(temporal, queue, "fixture-missing")
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)
