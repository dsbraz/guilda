"""A session refuses an actor the database refuses, and nothing else (PXOS-50).

Refusals of the actor, its delegation or its role become NotAuthorized; any other database
failure is not an authorization decision and reaches the caller as it is.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from guilda_kernel.identity import NotAuthorized, session


class Failing:
    """A pool whose connection fails when the session asks for the current actor."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator["Failing"]:
        yield self

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, query: str, *args: Any) -> str:
        if "current_actor" in query:
            raise self.error
        return "SELECT 1"


async def test_a_database_failure_is_not_an_authorization_refusal() -> None:
    pool = Failing(asyncpg.UndefinedFunctionError("pxos.current_actor() does not exist"))
    with pytest.raises(asyncpg.UndefinedFunctionError):
        async with session(pool, "erin"):  # type: ignore[arg-type]
            pass


async def test_the_unknown_actor_refusal_is_not_authorized(pool: asyncpg.Pool) -> None:
    with pytest.raises(NotAuthorized):
        async with session(pool, "nobody"):
            pass
