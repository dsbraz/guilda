"""A sync pass applies a connector's delta to the context memory (ADR 0006 §1, slice 10).

New and changed sources are ingested, deleted sources and those the connector lost access
to are forgotten, and each source's state and the connector's cursor are kept. The cursor
moves only when the whole pass succeeded, so whatever failed comes again in the next pass.
An unreachable connector marks its sources unavailable and removes nothing.
"""

from collections.abc import Sequence
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT

from pxos_kernel.context import FakeContextStore
from pxos_kernel.ingestion import FakeConnector, SourceDocument, sync

ACCESS = "access:store-3"


def connector() -> FakeConnector:
    return FakeConnector(f"fixture-{uuid4()}")


def document(source: str | None = None, version: str = "1") -> SourceDocument:
    return SourceDocument(
        source=source or f"fixture:shelf-log/{uuid4()}",
        version=version,
        text=f"Erin: restock aisle 3, version {version}.\n",
        access=(ACCESS,),
    )


async def sync_as_agent(pool: asyncpg.Pool, store: FakeContextStore, fake: FakeConnector) -> bool:
    return await sync(pool, store, fake, agent=AGENT)


async def states(pool: asyncpg.Pool, fake: FakeConnector) -> dict[str, str]:
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT source, state FROM pxos.source_states WHERE connector = $1", fake.name
        )
    return {row["source"]: row["state"] for row in rows}


async def cursor(pool: asyncpg.Pool, fake: FakeConnector) -> str | None:
    async with pool.acquire() as connection:
        value = await connection.fetchval(
            "SELECT cursor FROM pxos.connector_cursors WHERE connector = $1", fake.name
        )
    return None if value is None else str(value)


class BrokenStore(FakeContextStore):
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        raise ConnectionResetError("context memory unavailable")

    async def forget(self, source: str) -> None:
        raise ConnectionResetError("context memory unavailable")


async def test_a_pass_ingests_new_sources_and_keeps_the_cursor(pool: asyncpg.Pool) -> None:
    fake, store = connector(), FakeContextStore()
    first, second = document(), document()
    fake.put(first)
    fake.put(second)

    assert await sync_as_agent(pool, store, fake) is True
    assert await sync_as_agent(pool, store, fake) is True

    assert [key for key, _ in store.retained] == [first.source, second.source]
    assert await states(pool, fake) == {first.source: "available", second.source: "available"}
    assert await cursor(pool, fake) == "2"


async def test_a_changed_source_replaces_its_context_without_duplicating(
    pool: asyncpg.Pool,
) -> None:
    fake, store = connector(), FakeContextStore()
    first = document()
    fake.put(first)
    await sync_as_agent(pool, store, fake)
    fake.put(document(first.source, version="2"))

    await sync_as_agent(pool, store, fake)

    recalled = await store.recall("aisle 3", tags=[ACCESS])
    assert [(r.source, r.text) for r in recalled] == [
        (first.source, "Erin: restock aisle 3, version 2.\n")
    ]


@pytest.mark.parametrize(("change", "state"), [("delete", "deleted"), ("revoke", "no_access")])
async def test_a_deleted_or_revoked_source_is_forgotten(
    pool: asyncpg.Pool, change: str, state: str
) -> None:
    fake, store = connector(), FakeContextStore()
    kept, gone = document(), document()
    fake.put(kept)
    fake.put(gone)
    await sync_as_agent(pool, store, fake)
    getattr(fake, change)(gone.source)

    assert await sync_as_agent(pool, store, fake) is True

    assert {r.source for r in await store.recall("aisle 3", tags=[ACCESS])} == {kept.source}
    assert await states(pool, fake) == {kept.source: "available", gone.source: state}


async def test_a_source_forgotten_then_delivered_again_is_retained_again(
    pool: asyncpg.Pool,
) -> None:
    fake, store = connector(), FakeContextStore()
    doc = document()
    fake.put(doc)
    await sync_as_agent(pool, store, fake)
    fake.revoke(doc.source)
    await sync_as_agent(pool, store, fake)
    fake.put(doc)

    await sync_as_agent(pool, store, fake)

    assert {r.source for r in await store.recall("aisle 3", tags=[ACCESS])} == {doc.source}
    assert await states(pool, fake) == {doc.source: "available"}


async def test_an_unreachable_connector_marks_its_sources_unavailable_and_keeps_them(
    pool: asyncpg.Pool,
) -> None:
    fake, other, store = connector(), connector(), FakeContextStore()
    doc, elsewhere = document(), document()
    fake.put(doc)
    other.put(elsewhere)
    await sync_as_agent(pool, store, fake)
    await sync_as_agent(pool, store, other)
    fake.available = False

    assert await sync_as_agent(pool, store, fake) is False

    assert await states(pool, fake) == {doc.source: "unavailable"}
    assert await states(pool, other) == {elsewhere.source: "available"}
    assert {r.source for r in await store.recall("aisle 3", tags=[ACCESS])} >= {doc.source}
    assert await cursor(pool, fake) == "1"

    fake.available = True
    assert await sync_as_agent(pool, store, fake) is True
    assert await states(pool, fake) == {doc.source: "available"}


async def test_a_failure_to_retain_is_retried_in_the_next_pass(pool: asyncpg.Pool) -> None:
    fake, store = connector(), FakeContextStore()
    doc = document()
    fake.put(doc)
    with pytest.raises(ConnectionResetError):
        await sync_as_agent(pool, BrokenStore(), fake)
    assert await cursor(pool, fake) is None

    await sync_as_agent(pool, store, fake)
    await sync_as_agent(pool, store, fake)

    assert [key for key, _ in store.retained] == [doc.source]


class CannotRetain(FakeContextStore):
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        raise ValueError("refused")


async def test_a_failing_retain_does_not_hold_back_a_purge(pool: asyncpg.Pool) -> None:
    fake, store = connector(), CannotRetain()
    gone = document()
    store.memories[gone.source] = ("Erin: restock aisle 3.\n", frozenset({ACCESS}))
    fake.put(document())
    fake.delete(gone.source)

    with pytest.raises(ValueError):
        await sync_as_agent(pool, store, fake)

    assert await store.recall("aisle 3", tags=[ACCESS]) == []


async def test_a_failure_to_forget_is_retried_in_the_next_pass(pool: asyncpg.Pool) -> None:
    fake, store = connector(), FakeContextStore()
    doc = document()
    fake.put(doc)
    await sync_as_agent(pool, store, fake)
    fake.delete(doc.source)
    with pytest.raises(ConnectionResetError):
        await sync_as_agent(pool, BrokenStore(), fake)
    assert await states(pool, fake) == {doc.source: "available"}
    assert await cursor(pool, fake) == "1"

    await sync_as_agent(pool, store, fake)

    assert await store.recall("aisle 3", tags=[ACCESS]) == []
    assert await states(pool, fake) == {doc.source: "deleted"}
