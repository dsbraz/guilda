"""Reading canonical facts (K7): the facts of a type in force at two dates.

Facts are bitemporal: each revision is a full snapshot valid from ``valid_from`` and
known from ``known_at``. RLS decides which facts the session's actor can read.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from guilda_kernel.identity import Connection


@dataclass(frozen=True)
class Fact:
    entity_id: UUID
    revision: int
    package: str
    type_name: str
    payload: dict[str, Any]
    evidence: dict[str, str]
    valid_from: datetime
    proposal_id: UUID
    known_at: datetime


async def current(
    connection: Connection,
    package: str,
    type_name: str,
    *,
    valid_at: datetime,
    known_at: datetime,
) -> list[Fact]:
    """Every entity of a type whose fact is valid at ``valid_at`` as known at ``known_at``.

    Revisions are full snapshots from ``valid_from``. Per entity, among those known and
    already in force, the latest ``valid_from`` wins and, on the same date, the highest
    revision. RLS leaves out types the actor cannot read.
    """
    rows = await connection.fetch(
        """
        SELECT DISTINCT ON (entity_id) * FROM guilda.facts
        WHERE package = $1 AND type_name = $2 AND known_at <= $4 AND valid_from <= $3
        ORDER BY entity_id, valid_from DESC, revision DESC
        """,
        package,
        type_name,
        valid_at,
        known_at,
    )
    return [Fact(**dict(row)) for row in rows]
