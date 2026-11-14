"""Ingestion and literal-evidence proposals (PXOS-52, PXOS-54, PXOS-79).

A connector delivers source documents. Each source version is ingested once, by an agent
from its approved version: the source is kept in the canonical store, retained in the
context memory with the version's memory instructions, and the version's ADK step reads
each extracted fact into a candidate. The kernel proposes it only when the quote is
literally in the source text it holds, never in text the model returned.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import asyncpg
from google.adk.models.base_llm import BaseLlm

from pxos_kernel.agents import FactReader, approved_version, record_run
from pxos_kernel.context import ContextStore
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import session
from pxos_kernel.ontology import InvalidPackage, Registry
from pxos_kernel.state import Evidence, InvalidProposal, Proposal, propose


@dataclass(frozen=True)
class SourceDocument:
    source: str
    version: str
    text: str
    occurred_at: datetime

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


_WHITESPACE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def quote_in_source(quote: str, source_text: str) -> bool:
    """The quote appears verbatim in the source; only runs of whitespace are normalized."""
    wanted = _collapse(quote)
    return bool(wanted) and wanted in _collapse(source_text)


async def ingest(
    pool: asyncpg.Pool,
    registry: Registry,
    store: ContextStore,
    document: SourceDocument,
    model: BaseLlm,
    *,
    agent: str,
) -> list[Proposal]:
    """Ingest one source version as ``agent``, from its approved version; a replay returns
    no proposals.

    The version in force, the run record, the source row and the proposals belong to one
    transaction, so a failure leaves nothing behind and the same version can be ingested
    again. Retaining twice is harmless: the context memory keys the document by source and
    version.
    """
    async with session(pool, agent) as connection, connection.transaction():
        version = await approved_version(connection, agent)
        package, action = version.proposes
        registered = registry.get(package)
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
            return []
        await record_run(connection, version, document.key)
        await store.retain(document.key, document.text, version.memory_instructions)
        reader = FactReader(
            model, version, registered.models[registered.package.action(action).creates]
        )
        proposals = []
        for fact in await store.extracted(document.key):
            candidate = await reader.read(fact)
            if candidate is None or not quote_in_source(candidate[1], document.text):
                continue
            payload, quote = candidate
            try:
                async with connection.transaction():
                    proposals.append(
                        await propose(
                            connection,
                            registered,
                            action,
                            payload=payload,
                            evidence=Evidence(
                                source=document.source,
                                quote=quote,
                                context_ref=document.key,
                            ),
                            valid_from=document.occurred_at,
                        )
                    )
            except (InvalidPackage, InvalidProposal):
                continue
        return proposals
