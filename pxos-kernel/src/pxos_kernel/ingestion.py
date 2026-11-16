"""Ingestion into the context memory (PXOS-52, PXOS-79).

A connector delivers source documents. Each source version is ingested once, by an agent
from its approved version: the source is kept in the canonical store and retained in the
context memory with the version's memory instructions. Ingesting context needs no review
of each excerpt and proposes nothing.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

import asyncpg

from pxos_kernel.agents import approved_version, record_run
from pxos_kernel.context import ContextStore
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import session


@dataclass(frozen=True)
class SourceDocument:
    source: str
    version: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.source}@{self.version}"


class UnknownSource(PxosError):
    pass


class Connector(Protocol):
    def documents(self) -> Iterable[SourceDocument]: ...

    def fetch(self, key: str) -> SourceDocument:
        """The document ``source@version``; raises UnknownSource if there is none."""
        ...


class FakeConnector:
    """Delivers the given documents, in order, as a content connector would."""

    def __init__(self, documents: Iterable[SourceDocument]) -> None:
        self._documents = list(documents)

    def documents(self) -> Iterator[SourceDocument]:
        return iter(self._documents)

    def fetch(self, key: str) -> SourceDocument:
        for document in self._documents:
            if document.key == key:
                return document
        raise UnknownSource(f"source {key} not found")


async def ingest(
    pool: asyncpg.Pool, store: ContextStore, document: SourceDocument, *, agent: str
) -> bool:
    """Ingest one source version as ``agent``, from its approved version; false on a replay.

    The version in force, the run record and the source row belong to one transaction, so
    a failure leaves nothing behind and the same version can be ingested again. Retaining
    twice is harmless: the context memory keys the document by source and version.
    """
    async with session(pool, agent) as connection, connection.transaction():
        version = await approved_version(connection, agent)
        status = await connection.execute(
            """
            INSERT INTO pxos.sources (source, version, content) VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            document.source,
            document.version,
            document.text,
        )
        if status == "INSERT 0 0":
            return False
        await record_run(connection, version, document.key)
        await store.retain(document.key, document.text, version.memory_instructions)
        return True
