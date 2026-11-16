"""Ingestion into the context memory (PXOS-52, PXOS-79).

A source version is ingested once, as the agent and from its approved version: the
context memory retains it with the version's memory instructions, the run records the
version, and nothing is proposed. A failure leaves no trace, so the version can be
ingested again.
"""

from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, MEMORY_INSTRUCTIONS, RESTOCK_AGENT, installation

from agents_testing import publish
from db_testing import Database
from model_testing import ROUTE
from pxos_kernel.agents import NoApprovedVersion, approved_version, recorded_run
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import FakeConnector, SourceDocument, ingest
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

LOG = "Erin: I will restock aisle 3 with 12 boxes of A-1 tomorrow morning.\nCarla: thanks.\n"


def document() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:shelf-log/{uuid4()}",
        version="1",
        text=LOG,
    )


class BrokenStore(FakeContextStore):
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        raise ConnectionResetError("context memory unavailable")


@pytest.fixture
def unpublished(registry: Registry) -> Database:
    return installation("pxos_test_unpublished", registry, ROUTE)


@pytest.fixture
async def unpublished_pool(unpublished: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(unpublished.app_dsn)
    yield pool
    await pool.close()


async def proposals(database: Database) -> int:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        return int(await owner.fetchval("SELECT count(*) FROM pxos.proposals"))
    finally:
        await owner.close()


async def test_a_source_is_retained_with_the_versions_instructions_and_proposes_nothing(
    database: Database, pool: asyncpg.Pool
) -> None:
    doc = document()
    store = FakeContextStore()
    before = await proposals(database)

    assert await ingest(pool, store, doc, agent=AGENT) is True

    assert store.retained == [(doc.key, MEMORY_INSTRUCTIONS)]
    assert await proposals(database) == before


async def test_replay_of_a_source_version_retains_nothing(
    database: Database, pool: asyncpg.Pool
) -> None:
    doc = document()
    store = FakeContextStore()

    ingested = [
        await ingest(pool, store, d, agent=AGENT) for d in FakeConnector([doc, doc]).documents()
    ]

    assert ingested == [True, False]
    assert store.retained == [(doc.key, MEMORY_INSTRUCTIONS)]
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        rows = await owner.fetch(
            "SELECT ingested_by FROM pxos.sources WHERE source = $1", doc.source
        )
    finally:
        await owner.close()
    assert [r["ingested_by"] for r in rows] == [AGENT]


async def test_without_an_approved_version_nothing_is_ingested(
    unpublished: Database, unpublished_pool: asyncpg.Pool
) -> None:
    store = FakeContextStore()
    with pytest.raises(NoApprovedVersion):
        await ingest(unpublished_pool, store, document(), agent=AGENT)
    assert store.retained == []
    owner = await asyncpg.connect(unpublished.owner_dsn)
    try:
        assert await owner.fetchval("SELECT count(*) FROM pxos.sources") == 0
    finally:
        await owner.close()


async def test_each_run_records_the_version_it_ran(pool: asyncpg.Pool, registry: Registry) -> None:
    async with session(pool, AGENT) as connection:
        before = await approved_version(connection, AGENT)
    first = document()
    await ingest(pool, FakeContextStore(), first, agent=AGENT)

    changed = {**RESTOCK_AGENT, "instructions": f"{RESTOCK_AGENT['instructions']} Be brief."}
    await publish(pool, registry, changed, author="gina", approver="paula", change=before)
    second = document()
    await ingest(pool, FakeContextStore(), second, agent=AGENT)

    async with session(pool, AGENT) as connection:
        ran = [await recorded_run(connection, doc.key) for doc in (first, second)]
    assert [(r.definition, r.revision) for r in ran if r] == [
        (before.entity_id, before.revision),
        (before.entity_id, before.revision + 1),
    ]


async def test_a_failure_to_retain_leaves_no_trace_and_can_be_retried(pool: asyncpg.Pool) -> None:
    doc = document()
    with pytest.raises(ConnectionResetError):
        await ingest(pool, BrokenStore(), doc, agent=AGENT)
    async with session(pool, AGENT) as connection:
        assert await recorded_run(connection, doc.key) is None
    assert await ingest(pool, FakeContextStore(), doc, agent=AGENT) is True


async def test_the_source_ledger_keeps_no_content(database: Database) -> None:
    """Only which version was ingested, by whom and when: the text lives in the context
    memory alone, so forgetting a source has one place to purge."""
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        columns = await owner.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'pxos' AND table_name = 'sources'"
        )
    finally:
        await owner.close()
    assert {c["column_name"] for c in columns} == {
        "source",
        "version",
        "ingested_by",
        "ingested_at",
    }


async def test_application_cannot_read_the_source_ledger(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetch("SELECT source FROM pxos.sources")
