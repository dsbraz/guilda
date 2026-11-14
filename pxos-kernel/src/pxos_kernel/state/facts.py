"""Reading canonical facts (PXOS-7): as of two dates, per type, and an entity's history.

Facts are bitemporal: each revision is a full snapshot valid from ``valid_from`` and
known from ``known_at``. RLS decides which facts the session's actor can read.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pxos_kernel.identity import Connection
from pxos_kernel.state.canonical import Operation


@dataclass(frozen=True)
class Fact:
    entity_id: UUID
    revision: int
    package: str
    type_name: str
    operation: Operation
    payload: dict[str, Any]
    evidence: dict[str, str]
    valid_from: datetime
    valid_to: datetime | None
    proposal_id: UUID
    known_at: datetime


async def as_of(
    connection: Connection, entity_id: UUID, *, valid_at: datetime, known_at: datetime
) -> Fact | None:
    """The fact valid at ``valid_at`` as it was known at ``known_at``.

    Revisions are full snapshots from ``valid_from``. Among those known and already in
    force, the latest ``valid_from`` wins and, on the same date, the highest revision.
    An invalidation or an expired ``valid_to`` means no fact.
    """
    row = await connection.fetchrow(
        """
        SELECT * FROM pxos.facts
        WHERE entity_id = $1 AND known_at <= $3 AND valid_from <= $2
        ORDER BY valid_from DESC, revision DESC
        LIMIT 1
        """,
        entity_id,
        valid_at,
        known_at,
    )
    if row is None:
        return None
    fact = Fact(**dict(row))
    if fact.operation == "invalidate" or (fact.valid_to is not None and valid_at >= fact.valid_to):
        return None
    return fact


async def current(
    connection: Connection,
    package: str,
    type_name: str,
    *,
    valid_at: datetime,
    known_at: datetime,
) -> list[Fact]:
    """Every entity of a type whose fact is valid at ``valid_at`` as known at ``known_at``.

    Same rule as ``as_of``, per entity; RLS leaves out types the actor cannot read.
    """
    rows = await connection.fetch(
        """
        SELECT DISTINCT ON (entity_id) * FROM pxos.facts
        WHERE package = $1 AND type_name = $2 AND known_at <= $4 AND valid_from <= $3
        ORDER BY entity_id, valid_from DESC, revision DESC
        """,
        package,
        type_name,
        valid_at,
        known_at,
    )
    facts = [Fact(**dict(row)) for row in rows]
    return [
        fact
        for fact in facts
        if fact.operation != "invalidate" and (fact.valid_to is None or valid_at < fact.valid_to)
    ]


async def history(connection: Connection, entity_id: UUID) -> list[Fact]:
    rows = await connection.fetch(
        "SELECT * FROM pxos.facts WHERE entity_id = $1 ORDER BY revision", entity_id
    )
    return [Fact(**dict(row)) for row in rows]
