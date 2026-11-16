"""Ingestion into the context memory (PXOS-52, PXOS-79).

A connector delivers the changes to its sources since a cursor, and a sync pass applies
them (ADR 0006 §1). Each source version is ingested once, by an agent from its approved
version: the version is recorded, without its content, and the text is retained in the
context memory under the source, with the version's memory instructions and the access
tags the connector declared. Deleted sources, and those the connector lost access to, are
forgotten. Ingesting context needs no review of each excerpt and proposes nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import asyncpg

from pxos_kernel.agents import AgentVersion, approved_version, record_run
from pxos_kernel.context import ContextStore
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection, session


@dataclass(frozen=True)
class SourceDocument:
    source: str
    version: str
    text: str
    access: tuple[str, ...]
    """The access tags the connector declares; recall reaches the source only by them."""

    @property
    def key(self) -> str:
        return f"{self.source}@{self.version}"


class ConnectorUnavailable(PxosError):
    """The connector could not reach its source system."""


@dataclass(frozen=True)
class Changes:
    """A connector's delta since a cursor: the latest version of each new or changed source,
    the sources deleted and those it lost access to, and the cursor after them."""

    cursor: str
    documents: tuple[SourceDocument, ...] = ()
    deleted: tuple[str, ...] = ()
    revoked: tuple[str, ...] = ()


class Connector(Protocol):
    name: str

    async def changes(self, cursor: str | None) -> Changes:
        """The delta since ``cursor`` (from the start when None); raises ConnectorUnavailable
        when the source system cannot be reached."""
        ...


class FakeConnector:
    """A source system in memory: documents are put, deleted or revoked, and the delta
    since a cursor holds the last change to each source."""

    def __init__(self, name: str, documents: Iterable[SourceDocument] = ()) -> None:
        self.name = name
        self.available = True
        self._log: list[tuple[str, str, SourceDocument | None]] = []
        for document in documents:
            self.put(document)

    def put(self, document: SourceDocument) -> None:
        self._log.append((document.source, "put", document))

    def delete(self, source: str) -> None:
        self._log.append((source, "deleted", None))

    def revoke(self, source: str) -> None:
        self._log.append((source, "revoked", None))

    async def changes(self, cursor: str | None) -> Changes:
        if not self.available:
            raise ConnectorUnavailable(f"{self.name} cannot be reached")
        last = {source: (kind, doc) for source, kind, doc in self._log[int(cursor or 0) :]}
        return Changes(
            cursor=str(len(self._log)),
            documents=tuple(doc for _, doc in last.values() if doc is not None),
            deleted=tuple(source for source, (kind, _) in last.items() if kind == "deleted"),
            revoked=tuple(source for source, (kind, _) in last.items() if kind == "revoked"),
        )


async def ingest(
    pool: asyncpg.Pool,
    store: ContextStore,
    document: SourceDocument,
    *,
    agent: str,
    connector: str,
) -> bool:
    """Ingest one source version from ``connector`` as ``agent``, from its approved
    version; true when it was retained now, false when there was nothing to retain.

    The version in force, the run record, the source row and its pending state belong to
    one transaction, so a failure before the commit leaves nothing behind. The context
    memory retains the text under the source after the commit, replacing an older version;
    a failure there leaves the version pending, retained when it is ingested again. A
    replay of a retained version, or of one a newer version superseded, retains nothing.
    """
    async with session(pool, agent) as connection:
        version = await approved_version(connection, agent)
        if not await _pending(connection, version, document, connector):
            return False
    await store.retain(
        document.source, document.text, version.memory_instructions, tags=document.access
    )
    async with session(pool, agent) as connection:
        await connection.execute(
            """
            UPDATE pxos.source_states SET retained = true, state = 'available'
            WHERE source = $1 AND version = $2
            """,
            document.source,
            document.version,
        )
    return True


async def _pending(
    connection: Connection, version: AgentVersion, document: SourceDocument, connector: str
) -> bool:
    status = await connection.execute(
        "INSERT INTO pxos.sources (source, version) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        document.source,
        document.version,
    )
    if status == "INSERT 0 1":
        await record_run(connection, version, document.key)
        await connection.execute(
            """
            INSERT INTO pxos.source_states (source, connector, version, state)
            VALUES ($1, $2, $3, 'available')
            ON CONFLICT (source) DO UPDATE SET connector = EXCLUDED.connector,
                version = EXCLUDED.version, state = 'available', retained = false
            """,
            document.source,
            connector,
            document.version,
        )
        return True
    pending = await connection.fetchval(
        "SELECT NOT retained FROM pxos.source_states WHERE source = $1 AND version = $2",
        document.source,
        document.version,
    )
    return bool(pending)


async def sync(
    pool: asyncpg.Pool, store: ContextStore, connector: Connector, *, agent: str
) -> bool:
    """One pass over ``connector``'s delta as ``agent``; false when it could not be reached.

    Deleted and revoked sources are forgotten first, purged from the context memory before
    their state records it, so a source that fails to be retained holds back no purge; new
    and changed sources are ingested next. The cursor moves last, so a
    failure anywhere leaves it where it was and the next pass delivers the same changes
    again. An unreachable connector marks its sources unavailable and removes nothing.
    """
    async with session(pool, agent) as connection:
        cursor = await connection.fetchval(
            "SELECT cursor FROM pxos.connector_cursors WHERE connector = $1", connector.name
        )
    try:
        changes = await connector.changes(cursor)
    except ConnectorUnavailable:
        await _mark(pool, agent, connector.name, "available", "unavailable")
        return False
    for state, sources in (("deleted", changes.deleted), ("no_access", changes.revoked)):
        for source in sources:
            await store.forget(source)
            await _forget(pool, agent, connector.name, source, state)
    for document in changes.documents:
        await ingest(pool, store, document, agent=agent, connector=connector.name)
    await _mark(pool, agent, connector.name, "unavailable", "available")
    async with session(pool, agent) as connection:
        await connection.execute(
            """
            INSERT INTO pxos.connector_cursors (connector, cursor) VALUES ($1, $2)
            ON CONFLICT (connector) DO UPDATE SET cursor = EXCLUDED.cursor
            """,
            connector.name,
            changes.cursor,
        )
    return True


async def _forget(pool: asyncpg.Pool, agent: str, connector: str, source: str, state: str) -> None:
    async with session(pool, agent) as connection:
        await connection.execute(
            """
            INSERT INTO pxos.source_states (source, connector, state) VALUES ($1, $2, $3)
            ON CONFLICT (source) DO UPDATE SET state = EXCLUDED.state, retained = false
            """,
            source,
            connector,
            state,
        )


async def _mark(pool: asyncpg.Pool, agent: str, connector: str, old: str, new: str) -> None:
    async with session(pool, agent) as connection:
        await connection.execute(
            "UPDATE pxos.source_states SET state = $3 WHERE connector = $1 AND state = $2",
            connector,
            old,
            new,
        )
