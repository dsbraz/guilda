"""Outbox publisher (K52): deliver canonical events at least once.

An event is marked published only after its handler returns, in the same transaction that
locked it, so a failing handler gets the event again on the next run. Consumers must be
idempotent. Events without a handler are marked published: nobody is waiting for them.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from guilda_kernel.identity import Connection


@dataclass(frozen=True)
class Event:
    id: int
    event_type: str
    entity_id: UUID
    payload: dict[str, Any]
    created_at: datetime


Handler = Callable[[Event], Awaitable[None]]


async def publish(publisher: asyncpg.Pool, handlers: Mapping[str, Handler]) -> int:
    """Deliver pending events in order; return how many reached a handler."""
    delivered = 0
    async with publisher.acquire() as connection:
        while (reached := await _deliver_next(connection, handlers)) is not None:
            delivered += reached
    return delivered


async def _deliver_next(connection: Connection, handlers: Mapping[str, Handler]) -> int | None:
    """Deliver the oldest pending event; 1 if it reached a handler, None if none is pending."""
    async with connection.transaction():
        row = await connection.fetchrow(
            """
            SELECT id, event_type, entity_id, payload, created_at FROM guilda.outbox
            WHERE published_at IS NULL
            ORDER BY id LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        )
        if row is None:
            return None
        event = Event(**dict(row))
        handler = handlers.get(event.event_type)
        if handler is not None:
            await handler(event)
        await connection.execute(
            "UPDATE guilda.outbox SET published_at = now() WHERE id = $1", event.id
        )
        return 0 if handler is None else 1
