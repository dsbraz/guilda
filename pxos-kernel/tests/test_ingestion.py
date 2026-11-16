"""Ingestion into the context memory (PXOS-52, PXOS-79).

A source version is ingested once, as the agent and from its approved version: the
context memory retains it under the source, with the version's memory instructions and
the access the connector declared, the run records the version, and nothing is proposed.
The context memory retains only what was committed: a failure before the commit leaves no
trace, and a failure to retain after it leaves the source pending, retained when it comes
again.
"""

from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, MEMORY_INSTRUCTIONS, RESTOCK_AGENT, installation

from agents_testing import publish
from db_testing import Database
from fakes_testing import FakeContextStore
from model_testing import ROUTE
from pxos_kernel.agents import NoApprovedVersion, approved_version
from pxos_kernel.identity import session
from pxos_kernel.ingestion import SourceDocument, ingest
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

LOG = "Erin: I will restock aisle 3 with 12 boxes of A-1 tomorrow morning.\nCarla: thanks.\n"


CONNECTOR = "fixture"


def document(source: str | None = None, version: str = "1", text: str = LOG) -> SourceDocument:
    return SourceDocument(
        source=source or f"fixture:shelf-log/{uuid4()}",
        version=version,
        text=text,
        access=("access:store-3",),
    )


async def ingest_as_agent(store: FakeContextStore, pool: asyncpg.Pool, doc: SourceDocument) -> bool:
    return await ingest(pool, store, doc, agent=AGENT, connector=CONNECTOR)


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


async def test_a_source_is_retained_with_the_versions_instructions_and_access(
    database: Database, pool: asyncpg.Pool
) -> None:
    doc = document()
    store = FakeContextStore()
    before = await proposals(database)

    assert await ingest_as_agent(store, pool, doc) is True

    assert store.retained == [(doc.source, MEMORY_INSTRUCTIONS)]
    assert store.memories[doc.source] == (LOG, frozenset({"access:store-3"}))
    assert await proposals(database) == before


async def test_replay_of_a_source_version_retains_nothing(
    database: Database, pool: asyncpg.Pool
) -> None:
    doc = document()
    store = FakeContextStore()

    ingested = [await ingest_as_agent(store, pool, doc) for _ in range(2)]

    assert ingested == [True, False]
    assert store.retained == [(doc.source, MEMORY_INSTRUCTIONS)]
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
        await ingest_as_agent(store, unpublished_pool, document())
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
    await ingest_as_agent(FakeContextStore(), pool, first)

    changed = {**RESTOCK_AGENT, "instructions": f"{RESTOCK_AGENT['instructions']} Be brief."}
    await publish(pool, registry, changed, author="gina", approver="paula", change=before)
    second = document()
    await ingest_as_agent(FakeContextStore(), pool, second)

    async with session(pool, AGENT) as connection:
        ran = [
            await connection.fetchrow(
                "SELECT definition, revision FROM pxos.agent_runs WHERE context_ref = $1", doc.key
            )
            for doc in (first, second)
        ]
    assert [(r["definition"], r["revision"]) for r in ran if r] == [
        (before.entity_id, before.revision),
        (before.entity_id, before.revision + 1),
    ]


class CommittedOnly(FakeContextStore):
    """Retains only after checking, from another connection, that the version committed."""

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database

    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        owner = await asyncpg.connect(self.database.owner_dsn)
        try:
            assert await owner.fetchval("SELECT count(*) FROM pxos.sources WHERE source = $1", key)
        finally:
            await owner.close()
        await super().retain(key, text, instructions, tags=tags)


async def test_the_context_memory_retains_only_what_was_committed(
    database: Database, pool: asyncpg.Pool
) -> None:
    store = CommittedOnly(database)
    assert await ingest_as_agent(store, pool, document()) is True
    assert len(store.retained) == 1


async def runs(database: Database, context_ref: str) -> int:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        return int(
            await owner.fetchval(
                "SELECT count(*) FROM pxos.agent_runs WHERE context_ref = $1", context_ref
            )
        )
    finally:
        await owner.close()


async def test_a_failure_to_retain_leaves_the_source_pending_until_it_comes_again(
    database: Database, pool: asyncpg.Pool
) -> None:
    doc = document()
    with pytest.raises(ConnectionResetError):
        await ingest_as_agent(BrokenStore(), pool, doc)
    async with pool.acquire() as connection:
        pending = await connection.fetchrow(
            "SELECT connector, version, state, retained FROM pxos.source_states WHERE source = $1",
            doc.source,
        )
    assert dict(pending or {}) == {
        "connector": CONNECTOR,
        "version": "1",
        "state": "available",
        "retained": False,
    }

    store = FakeContextStore()
    assert [await ingest_as_agent(store, pool, doc) for _ in range(2)] == [True, False]
    assert store.retained == [(doc.source, MEMORY_INSTRUCTIONS)]
    assert await runs(database, doc.key) == 1


async def test_a_pending_version_superseded_by_a_newer_one_is_not_retained(
    pool: asyncpg.Pool,
) -> None:
    first = document()
    second = document(first.source, version="2", text="Erin: aisle 3 is done.\n")
    with pytest.raises(ConnectionResetError):
        await ingest_as_agent(BrokenStore(), pool, first)
    store = FakeContextStore()

    assert await ingest_as_agent(store, pool, second) is True
    assert await ingest_as_agent(store, pool, first) is False
    assert store.memories[first.source][0] == second.text


async def test_the_database_refuses_an_unknown_source_state(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "INSERT INTO pxos.source_states (source, connector, state)"
                " VALUES ('fixture:x', 'fixture', 'forgotten')"
            )


@pytest.mark.parametrize("table", ["source_states", "connector_cursors"])
async def test_application_cannot_delete_source_states_or_cursors(
    pool: asyncpg.Pool, table: str
) -> None:
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(f"DELETE FROM pxos.{table}")


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
