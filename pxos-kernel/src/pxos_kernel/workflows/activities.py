"""Activities: where workflows touch the kernel, always in an actor's database session."""

from collections.abc import Callable, Iterable
from typing import Any

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from pxos_kernel.context import ContextStore, ContextUnavailable
from pxos_kernel.ingestion import Connector, sync

TRANSIENT = (OSError, TimeoutError, asyncpg.PostgresConnectionError, ContextUnavailable)
"""Failures worth retrying. Anything else fails the workflow, which can be started again
with the same id: a defect or bad data does not fix itself on retry."""


class SyncActivities:
    """Run one sync pass of a connector, by name, as ``agent``.

    Only references cross into the workflow: the connector's name in, a flag out. A
    failure is reported by its type alone, because its message may quote a source,
    whether it came from the connector or from the context memory.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        store: ContextStore,
        connectors: Iterable[Connector],
        *,
        agent: str,
    ) -> None:
        self._pool = pool
        self._store = store
        self._connectors = {connector.name: connector for connector in connectors}
        self._agent = agent

    @activity.defn(name="sync_connector")
    async def sync_connector(self, name: str) -> bool:
        try:
            return await self.sync(self._connectors[name])
        except Exception as error:
            raise ApplicationError(
                f"sync of {name} failed: {type(error).__name__}",
                non_retryable=not isinstance(error, TRANSIENT),
            ) from None

    async def sync(self, connector: Connector) -> bool:
        return await sync(self._pool, self._store, connector, agent=self._agent)

    def registered(self) -> list[Callable[..., Any]]:
        return [self.sync_connector]
