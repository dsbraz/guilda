"""Ingestion runs as a workflow that carries references only (PXOS-70, ADR 0004 §2).

The workflow receives a source reference and an id derived from it. The Activity fetches
the document from the connector and ingests it as the agent; what it hands back to the
workflow is a flag. A retry after the commit ingests nothing twice, and nothing the
source says reaches the Temporal history.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from pxos_kernel.context import FakeContextStore
from pxos_kernel.ingestion import FakeConnector, SourceDocument
from pxos_kernel.workflows import (
    IngestionActivities,
    IngestSource,
    ingest_workflow_id,
    workflow_runner,
)
from temporal_testing import history_bytes

SECRET = "restock aisle 3 with 12 boxes"
LOG = f"Erin: I will {SECRET} of A-1 tomorrow.\nGina: I will restock aisle 9 with 4 of B-2.\n"


def source() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:shelf-log/{uuid4()}",
        version="1",
        text=LOG,
        occurred_at=datetime(2026, 11, 14, 9, tzinfo=UTC),
    )


class Store(FakeContextStore):
    """Fails the first ``failures`` retains, quoting the text as a careless client would."""

    def __init__(self, failures: int = 0, error: type[Exception] = ConnectionResetError) -> None:
        super().__init__()
        self.failures = failures
        self.error = error

    async def retain(self, key: str, text: str, instructions: str) -> None:
        if self.failures:
            self.failures -= 1
            raise self.error(f"cannot retain {text}")
        await super().retain(key, text, instructions)


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@asynccontextmanager
async def worker(
    temporal: Client,
    pool: asyncpg.Pool,
    document: SourceDocument,
    store: FakeContextStore,
    activities: type[IngestionActivities] = IngestionActivities,
) -> AsyncIterator[str]:
    queue = f"ingest-{uuid4()}"
    ingestion = activities(pool, store, FakeConnector([document]), agent=AGENT)
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[IngestSource],
        activities=ingestion.registered(),
        workflow_runner=workflow_runner(),
    ):
        yield queue


async def start(temporal: Client, queue: str, ref: str) -> WorkflowHandle[IngestSource, bool]:
    return await temporal.start_workflow(
        IngestSource.run, ref, id=ingest_workflow_id(ref), task_queue=queue
    )


async def test_the_workflow_ingests_by_reference(temporal: Client, pool: asyncpg.Pool) -> None:
    document = source()
    store = Store()
    async with worker(temporal, pool, document, store) as queue:
        handle = await start(temporal, queue, document.key)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start(temporal, queue, document.key)
        assert await asyncio.wait_for(handle.result(), timeout=30) is True

    assert [key for key, _ in store.retained] == [document.key]
    raw = await history_bytes(temporal, handle.id)
    assert document.key.encode() in raw  # the search sees what history holds
    assert SECRET.encode() not in raw


class CrashesAfterCommit(IngestionActivities):
    """The first attempt commits, then dies before the workflow hears of it."""

    async def ingest_document(self, document: SourceDocument) -> bool:
        ingested = await super().ingest_document(document)
        if activity.info().attempt == 1:
            raise ConnectionResetError("worker lost after commit")
        return ingested


async def test_a_retry_after_the_commit_ingests_nothing_twice(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    document = source()
    store = Store()
    async with worker(temporal, pool, document, store, CrashesAfterCommit) as queue:
        handle = await start(temporal, queue, document.key)
        assert await asyncio.wait_for(handle.result(), timeout=30) is False

    assert [key for key, _ in store.retained] == [document.key]


async def test_a_transient_failure_is_retried(temporal: Client, pool: asyncpg.Pool) -> None:
    document = source()
    store = Store(failures=1)
    async with worker(temporal, pool, document, store) as queue:
        handle = await start(temporal, queue, document.key)
        assert await asyncio.wait_for(handle.result(), timeout=30) is True

    assert [key for key, _ in store.retained] == [document.key]


async def test_a_failure_does_not_carry_the_source_into_history(
    temporal: Client, pool: asyncpg.Pool
) -> None:
    document = source()
    async with worker(temporal, pool, document, Store(failures=1, error=ValueError)) as queue:
        handle = await start(temporal, queue, document.key)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)

    assert SECRET.encode() not in await history_bytes(temporal, handle.id)


async def test_an_unknown_source_fails_the_workflow(temporal: Client, pool: asyncpg.Pool) -> None:
    async with worker(temporal, pool, source(), Store()) as queue:
        handle = await start(temporal, queue, "fixture:shelf-log/missing@1")
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)
