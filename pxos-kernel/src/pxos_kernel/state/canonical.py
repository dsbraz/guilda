"""Governed canonical state (PXOS-7): proposal -> review -> versioned fact.

Pure rules (digest, evidence) live here as functions; the database enforces the
invariants that protect data (reviewer, approver role, self-approval, append-only).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection, NotAuthorized
from pxos_kernel.ontology import RegisteredPackage, Registry

Decision = Literal["approved", "rejected"]
Operation = Literal["assert", "invalidate"]


class InvalidProposal(PxosError):
    """The proposal is incomplete or inconsistent."""


class DigestMismatch(PxosError):
    """The reviewer did not review exactly this proposal."""


class SelfApproval(NotAuthorized):
    """The author (or whoever they act for) tried to review their own proposal."""


class NotApprover(NotAuthorized):
    """The reviewer does not hold the approver role declared by the package."""


class StalePackage(PxosError):
    """The package changed after the proposal; a new proposal is needed."""


class StaleRevision(PxosError):
    """Another proposal on the same revision was approved first."""


class DecisionFinal(PxosError):
    """The proposal already has a different final decision."""


class PersonRequired(NotAuthorized):
    """Only a person acting for themselves reviews; never an agent (PXOS-88)."""


@dataclass(frozen=True)
class Evidence:
    source: str
    quote: str
    context_ref: str

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "quote": self.quote, "context_ref": self.context_ref}


@dataclass(frozen=True)
class Proposal:
    id: UUID
    package: str
    package_version: str
    package_digest: str
    action: str
    type_name: str
    entity_id: UUID
    base_revision: int
    operation: Operation
    valid_from: datetime
    valid_to: datetime | None
    payload: dict[str, Any]
    evidence: dict[str, str]
    digest: str
    author: str
    author_principal: str
    created_at: datetime


@dataclass(frozen=True)
class Review:
    proposal_id: UUID
    decision: Decision
    reason: str | None
    proposal_digest: str
    reviewer: str
    reviewer_principal: str
    decided_at: datetime


def proposal_digest(content: dict[str, Any]) -> str:
    """Identity of exactly what a reviewer sees; any change produces another digest."""
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def check_evidence(evidence: Evidence) -> None:
    for name, value in evidence.as_dict().items():
        if not value.strip():
            raise InvalidProposal(f"evidence {name} is required")


async def propose(
    connection: Connection,
    registered: RegisteredPackage,
    action: str,
    *,
    payload: dict[str, Any],
    evidence: Evidence,
    valid_from: datetime,
    valid_to: datetime | None = None,
    entity_id: UUID | None = None,
    base_revision: int = 0,
) -> Proposal:
    """Propose asserting a full snapshot of an entity from ``valid_from``."""
    action_type = registered.package.action(action)
    if (entity_id is None) != (base_revision == 0):
        raise InvalidProposal("a new entity has base revision 0; a change names its entity")
    payload = registered.validate(action_type.creates, payload)
    await _check_references(connection, registered, action_type.creates, payload)
    return await _insert(
        connection,
        registered,
        action,
        action_type.creates,
        entity_id or uuid4(),
        base_revision,
        "assert",
        valid_from,
        valid_to,
        payload,
        evidence,
    )


async def propose_invalidation(
    connection: Connection,
    registered: RegisteredPackage,
    action: str,
    *,
    entity_id: UUID,
    base_revision: int,
    valid_from: datetime,
    evidence: Evidence,
) -> Proposal:
    """Propose that an entity stops being valid from ``valid_from``; history stays."""
    if base_revision < 1:
        raise InvalidProposal("only an existing fact can be invalidated")
    action_type = registered.package.action(action)
    return await _insert(
        connection,
        registered,
        action,
        action_type.creates,
        entity_id,
        base_revision,
        "invalidate",
        valid_from,
        None,
        {},
        evidence,
    )


async def review(
    connection: Connection,
    registry: Registry,
    proposal_id: UUID,
    decision: Decision,
    *,
    digest: str,
    reason: str | None = None,
) -> Review:
    """Record the session actor's decision on exactly the proposal they reviewed."""
    proposal = await get_proposal(connection, proposal_id)
    if registry.get(proposal.package).package.digest != proposal.package_digest:
        raise StalePackage(f"{proposal.package} changed after proposal {proposal_id}")
    try:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO pxos.reviews (proposal_id, decision, reason, proposal_digest,
                                          reviewer, reviewer_principal)
                VALUES ($1, $2, $3, $4, '', '')
                RETURNING *
                """,
                proposal_id,
                decision,
                reason,
                digest,
            )
    except asyncpg.UniqueViolationError as error:
        if error.constraint_name != "reviews_pkey":
            raise _translate(error) from None
        existing = await _review(connection, proposal_id)
        if existing.decision != decision or existing.proposal_digest != digest:
            raise DecisionFinal(f"proposal {proposal_id} was already {existing.decision}") from None
        return existing
    except asyncpg.PostgresError as error:
        raise _translate(error) from None
    assert row is not None
    return Review(**dict(row))


async def get_proposal(connection: Connection, proposal_id: UUID) -> Proposal:
    row = await connection.fetchrow("SELECT * FROM pxos.proposals WHERE id = $1", proposal_id)
    if row is None:
        raise InvalidProposal(f"proposal {proposal_id} not found")
    return Proposal(**dict(row))


async def proposals_from(connection: Connection, context_ref: str) -> list[Proposal]:
    """The proposals the session can read whose evidence points at ``context_ref``."""
    rows = await connection.fetch(
        """
        SELECT * FROM pxos.proposals WHERE evidence->>'context_ref' = $1
        ORDER BY created_at, id
        """,
        context_ref,
    )
    return [Proposal(**dict(row)) for row in rows]


async def get_review(connection: Connection, proposal_id: UUID) -> Review | None:
    """The decision on a proposal the session can read, or None while it is pending."""
    row = await connection.fetchrow(
        "SELECT * FROM pxos.reviews WHERE proposal_id = $1", proposal_id
    )
    return None if row is None else Review(**dict(row))


async def _insert(
    connection: Connection,
    registered: RegisteredPackage,
    action: str,
    type_name: str,
    entity_id: UUID,
    base_revision: int,
    operation: Operation,
    valid_from: datetime,
    valid_to: datetime | None,
    payload: dict[str, Any],
    evidence: Evidence,
) -> Proposal:
    check_evidence(evidence)
    package = registered.package
    content = {
        "package": package.name,
        "package_version": package.version,
        "package_digest": package.digest,
        "action": action,
        "type_name": type_name,
        "entity_id": str(entity_id),
        "base_revision": base_revision,
        "operation": operation,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat() if valid_to else None,
        "payload": payload,
        "evidence": evidence.as_dict(),
    }
    try:
        row = await connection.fetchrow(
            """
            INSERT INTO pxos.proposals (
                package, package_version, package_digest, action, type_name, entity_id,
                base_revision, operation, valid_from, valid_to, payload, evidence, digest,
                author, author_principal
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, '', '')
            RETURNING *
            """,
            package.name,
            package.version,
            package.digest,
            action,
            type_name,
            entity_id,
            base_revision,
            operation,
            valid_from,
            valid_to,
            payload,
            evidence.as_dict(),
            proposal_digest(content),
        )
    except asyncpg.PostgresError as error:
        raise _translate(error) from None
    assert row is not None
    return Proposal(**dict(row))


async def _check_references(
    connection: Connection, registered: RegisteredPackage, type_name: str, payload: dict[str, Any]
) -> None:
    """A slot the package marks as referring to an actor must name an existing actor."""
    for slot in registered.package.references.get(type_name, {}):
        value = payload.get(slot)
        if value is None:
            continue
        known = await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pxos.actors WHERE id = $1)", value
        )
        if not known:
            raise InvalidProposal(f"{type_name}.{slot}: unknown actor {value}")


async def _review(connection: Connection, proposal_id: UUID) -> Review:
    row = await connection.fetchrow(
        "SELECT * FROM pxos.reviews WHERE proposal_id = $1", proposal_id
    )
    assert row is not None
    return Review(**dict(row))


REFUSALS: dict[str, type[PxosError]] = {
    "PX001": NotAuthorized,
    "PX002": NotAuthorized,
    "PX010": DigestMismatch,
    "PX011": SelfApproval,
    "PX012": StalePackage,
    "PX013": NotApprover,
    "PX014": InvalidProposal,
    "PX018": PersonRequired,
}


def _translate(error: asyncpg.PostgresError) -> PxosError:
    """Map database refusals by SQLSTATE and constraint name, never by message text."""
    refusal = REFUSALS.get(error.sqlstate)
    if refusal is not None:
        return refusal(str(error))
    if isinstance(error, asyncpg.UniqueViolationError) and error.constraint_name == "facts_pkey":
        return StaleRevision(str(error))
    if isinstance(error, asyncpg.CheckViolationError):
        return InvalidProposal(str(error))
    raise error
