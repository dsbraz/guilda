"""Ingestion runs as a workflow that carries references only (PXOS-70, ADR 0004 §2).

The workflow receives a source reference and an id derived from it. The Activity fetches
the document from the connector and ingests it as the agent; what it hands back to the
workflow is proposal ids. A retry after the commit returns the same proposals, and
nothing the source says, not even the evidence quote, reaches the Temporal history.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from db_testing import Database
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import Candidate, FakeConnector, Reader, SourceDocument
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool, proposals_from, publish, review
from pxos_kernel.workflows import (
    AwaitReview,
    IngestionActivities,
    IngestSource,
    ReviewActivities,
    ingest_workflow_id,
    review_signals,
    workflow_runner,
)
from temporal_testing import history_bytes

AGENT = "restock-agent"
SECRET = "restock aisle 3 with 12 boxes"
LOG = f"Erin: I will {SECRET} of A-1 tomorrow.\nGina: I will restock aisle 9 with 4 of B-2.\n"
QUOTES = {"A-1": f"Erin: I will {SECRET} of A-1", "B-2": "Gina: I will restock aisle 9"}


def read(fact: str) -> Candidate | None:
    sku = fact.removeprefix("RESTOCK ")
    return Candidate(
        action="create_restock_order",
        payload={"sku": sku, "quantity": 1, "occurred_at": "2026-11-14T09:00:00Z"},
        quote=QUOTES[sku],
    )


def source() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:shelf-log/{uuid4()}",
        version="1",
        text=LOG,
        occurred_at=datetime(2026, 11, 14, 9, tzinfo=UTC),
    )


def store_for(document: SourceDocument) -> FakeContextStore:
    return FakeContextStore({document.key: ["RESTOCK A-1", "RESTOCK B-2"]})


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    yield pool
    await pool.close()


@asynccontextmanager
async def worker(
    temporal: Client,
    pool: asyncpg.Pool,
    registry: Registry,
    document: SourceDocument,
    *,
    reader: Reader = read,
    activities: type[IngestionActivities] = IngestionActivities,
) -> AsyncIterator[str]:
    queue = f"ingest-{uuid4()}"
    ingestion = activities(
        pool,
        registry,
        store_for(document),
        FakeConnector([document]),
        package="retail",
        instructions="Extract restock promises.",
        reader=reader,
        actor=AGENT,
    )
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[IngestSource, AwaitReview],
        activities=ingestion.registered() + ReviewActivities(pool, actor=AGENT).registered(),
        workflow_runner=workflow_runner(),
    ):
        yield queue


async def start(
    temporal: Client, queue: str, ref: str
) -> WorkflowHandle[IngestSource, dict[str, str]]:
    return await temporal.start_workflow(
        IngestSource.run, ref, id=ingest_workflow_id(ref), task_queue=queue
    )


async def proposed(pool: asyncpg.Pool, document: SourceDocument, count: int) -> list[UUID]:
    """The ids of the proposals ingested from ``document``, once there are ``count``."""
    for _ in range(300):
        async with session(pool, AGENT) as connection:
            found = await proposals_from(connection, document.key)
        if len(found) >= count:
            return [p.id for p in found]
        await asyncio.sleep(0.1)
    raise AssertionError(f"expected {count} proposals from {document.key}")


async def decide(
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    temporal: Client,
    registry: Registry,
    decisions: dict[UUID, str],
) -> None:
    async with session(pool, "carla") as connection:
        for proposal_id, decision in decisions.items():
            async with connection.transaction():
                digest = await connection.fetchval(
                    "SELECT digest FROM pxos.proposals WHERE id = $1", proposal_id
                )
                await review(
                    connection,
                    registry,
                    proposal_id,
                    decision,  # type: ignore[arg-type]
                    digest=digest,
                    reason="não precisa" if decision == "rejected" else None,
                )
    await publish(publisher, review_signals(temporal))


async def test_the_workflow_ingests_by_reference_and_waits_for_each_decision(
    temporal: Client,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
) -> None:
    document = source()
    async with worker(temporal, pool, registry, document) as queue:
        handle = await start(temporal, queue, document.key)
        with pytest.raises(WorkflowAlreadyStartedError):
            await start(temporal, queue, document.key)
        first, second = await proposed(pool, document, 2)
        await decide(pool, publisher, temporal, registry, {first: "approved", second: "rejected"})
        result = await asyncio.wait_for(handle.result(), timeout=30)

    assert result == {str(first): "approved", str(second): "rejected"}
    raw = await history_bytes(temporal, handle.id)
    assert str(first).encode() in raw  # the search sees what history holds
    assert SECRET.encode() not in raw
    assert document.text.encode() not in raw


class CrashesAfterCommit(IngestionActivities):
    """The first attempt commits, then dies before the workflow hears of it."""

    async def ingest_document(self, document: SourceDocument) -> list[UUID]:
        ids = await super().ingest_document(document)
        if activity.info().attempt == 1:
            raise ConnectionResetError("worker lost after commit")
        return ids


async def test_a_retry_after_the_commit_returns_the_same_proposals(
    temporal: Client,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
) -> None:
    document = source()
    async with worker(temporal, pool, registry, document, activities=CrashesAfterCommit) as queue:
        handle = await start(temporal, queue, document.key)
        ids = await proposed(pool, document, 2)
        await decide(pool, publisher, temporal, registry, dict.fromkeys(ids, "approved"))
        result = await asyncio.wait_for(handle.result(), timeout=30)

    assert sorted(result) == sorted(str(i) for i in ids)
    assert await proposed(pool, document, 2) == ids  # no duplicates


async def test_a_failure_does_not_carry_the_source_into_history(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    document = source()

    def broken(fact: str) -> Candidate | None:
        raise ValueError(f"cannot read {read(fact)}")  # the message quotes the source

    async with worker(temporal, pool, registry, document, reader=broken) as queue:
        handle = await start(temporal, queue, document.key)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)

    assert SECRET.encode() not in await history_bytes(temporal, handle.id)


async def test_an_unknown_source_fails_the_workflow(
    temporal: Client, pool: asyncpg.Pool, registry: Registry
) -> None:
    async with worker(temporal, pool, registry, source()) as queue:
        handle = await start(temporal, queue, "fixture:shelf-log/missing@1")
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=30)
