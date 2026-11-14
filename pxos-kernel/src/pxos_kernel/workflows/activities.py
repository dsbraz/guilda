"""Activities: where workflows touch the kernel, always in an actor's database session."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from pxos_kernel.identity import session
from pxos_kernel.state import InvalidProposal, get_proposal, get_review


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
