"""Activities: where workflows touch the kernel, always in an actor's database session."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from pxos_kernel.context import ContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import Connector, Reader, SourceDocument, UnknownSource, ingest
from pxos_kernel.ontology import Registry
from pxos_kernel.state import InvalidProposal, get_proposal, get_review, proposals_from

TRANSIENT = (OSError, TimeoutError, asyncpg.PostgresConnectionError)
"""Failures worth retrying. Anything else fails the workflow, which can be started again
with the same id: a defect or bad data does not fix itself on retry."""


class ReviewActivities:
    """Read decisions as ``actor``, the agent the worker runs for."""

    def __init__(self, pool: asyncpg.Pool, *, actor: str) -> None:
        self._pool = pool
        self._actor = actor

    @activity.defn(name="decision_of")
    async def decision_of(self, proposal_id: str) -> str | None:
        async with session(self._pool, self._actor) as connection:
            try:
                await get_proposal(connection, UUID(proposal_id))
            except InvalidProposal:
                raise ApplicationError(
                    f"{self._actor} cannot read proposal {proposal_id}", non_retryable=True
                ) from None
            found = await get_review(connection, UUID(proposal_id))
        return None if found is None else found.decision

    def registered(self) -> list[Callable[..., Any]]:
        return [self.decision_of]


class IngestionActivities:
    """Fetch a source by reference and ingest it as ``actor``, the agent the worker runs for.

    Only references cross into the workflow: the source reference in, proposal ids out.
    A failure is reported by its type alone, because its message may quote the source.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        registry: Registry,
        store: ContextStore,
        connector: Connector,
        *,
        package: str,
        instructions: str,
        reader: Reader,
        actor: str,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._store = store
        self._connector = connector
        self._package = package
        self._instructions = instructions
        self._reader = reader
        self._actor = actor

    @activity.defn(name="ingest_source")
    async def ingest_source(self, ref: str) -> list[str]:
        try:
            document = self._connector.fetch(ref)
        except UnknownSource:
            raise ApplicationError(f"source {ref} not found", non_retryable=True) from None
        try:
            proposal_ids = await self.ingest_document(document)
        except Exception as error:
            raise ApplicationError(
                f"ingestion of {ref} failed: {type(error).__name__}",
                non_retryable=not isinstance(error, TRANSIENT),
            ) from None
        return [str(proposal_id) for proposal_id in proposal_ids]

    async def ingest_document(self, document: SourceDocument) -> list[UUID]:
        """Ingest once; a retry after the commit finds the proposals by their evidence."""
        created = await ingest(
            self._pool,
            self._registry,
            self._store,
            document,
            package=self._package,
            instructions=self._instructions,
            reader=self._reader,
            actor=self._actor,
        )
        if created:
            return [proposal.id for proposal in created]
        async with session(self._pool, self._actor) as connection:
            return [proposal.id for proposal in await proposals_from(connection, document.key)]

    def registered(self) -> list[Callable[..., Any]]:
        return [self.ingest_source]
