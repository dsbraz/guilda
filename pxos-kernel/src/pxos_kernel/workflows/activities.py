"""Activities: where workflows touch the kernel, always in an actor's database session."""

from collections.abc import Callable
from typing import Any

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from pxos_kernel.context import ContextStore
from pxos_kernel.ingestion import Connector, SourceDocument, UnknownSource, ingest

TRANSIENT = (OSError, TimeoutError, asyncpg.PostgresConnectionError)
"""Failures worth retrying. Anything else fails the workflow, which can be started again
with the same id: a defect or bad data does not fix itself on retry."""


class IngestionActivities:
    """Fetch a source by reference and ingest it as ``agent``, from its approved version.

    Only references cross into the workflow: the source reference in, a flag out. A
    failure is reported by its type alone, because its message may quote the source.
    """

    def __init__(
        self, pool: asyncpg.Pool, store: ContextStore, connector: Connector, *, agent: str
    ) -> None:
        self._pool = pool
        self._store = store
        self._connector = connector
        self._agent = agent

    @activity.defn(name="ingest_source")
    async def ingest_source(self, ref: str) -> bool:
        try:
            document = self._connector.fetch(ref)
        except UnknownSource:
            raise ApplicationError(f"source {ref} not found", non_retryable=True) from None
        try:
            return await self.ingest_document(document)
        except Exception as error:
            raise ApplicationError(
                f"ingestion of {ref} failed: {type(error).__name__}",
                non_retryable=not isinstance(error, TRANSIENT),
            ) from None

    async def ingest_document(self, document: SourceDocument) -> bool:
        return await ingest(self._pool, self._store, document, agent=self._agent)

    def registered(self) -> list[Callable[..., Any]]:
        return [self.ingest_source]
