"""Agent definitions as approved versions (PXOS-78, K34; ADR 0004 §3).

A definition is a canonical type of the kernel's own package, so it goes through the same
proposal cycle as any fact: a draft is a pending proposal, platform-admin approves, and
each approved revision of the agent's entity is an immutable version. Only the version
in force starts a cycle, which pins it by entity and revision until it ends.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

from pxos_kernel.agents.package import generated
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection, session
from pxos_kernel.ontology import RegisteredPackage, Registry
from pxos_kernel.state import Evidence, Fact, Proposal, current, get_proposal, propose

AGENTS = "pxos_agents"
PACKAGE_DIR = Path(__file__).resolve().parent / "package"


class NoApprovedVersion(PxosError):
    pass


class AmbiguousDefinition(PxosError):
    """More than one entity defines the agent; which one runs is not for the kernel to guess."""


@dataclass(frozen=True)
class AgentVersion:
    agent: str
    entity_id: UUID
    revision: int
    digest: str
    """The digest of the proposal the approver reviewed."""
    instructions: str
    memory_instructions: str
    tools: tuple[str, ...]
    recall_tags: tuple[str, ...]
    """The scope the kernel binds to recall and to what the cycle retains."""
    schedule: str
    """When a cycle starts, as a five-field cron expression."""
    answer_deadline_hours: int
    max_rounds: int
    thread: str
    deliverable: tuple[str, str]
    """The package and action that propose the cycle's deliverable."""


def register_agents(registry: Registry) -> RegisteredPackage:
    """Register the kernel's agents package; the composition root calls it."""
    return registry.register(PACKAGE_DIR, generated)


async def approved_version(connection: Connection, agent: str) -> AgentVersion:
    """The version of ``agent`` in force now, as the session can read it."""
    now = datetime.now(UTC)
    facts = [
        fact
        for fact in await current(connection, AGENTS, "AgentVersion", valid_at=now, known_at=now)
        if fact.payload["agent"] == agent
    ]
    if not facts:
        raise NoApprovedVersion(f"{agent} has no approved version")
    if len(facts) > 1:
        raise AmbiguousDefinition(f"{agent} is defined by {len(facts)} entities")
    return await _version(connection, facts[0])


async def pinned_version(
    connection: Connection, agent: str, entity_id: UUID, revision: int
) -> AgentVersion:
    """The version of ``agent`` a cycle pinned: revision ``revision`` of ``entity_id``,
    whatever is in force now, as the session can read it."""
    row = await connection.fetchrow(
        """
        SELECT * FROM pxos.facts WHERE entity_id = $1 AND revision = $2
          AND package = $3 AND type_name = 'AgentVersion' AND payload->>'agent' = $4
        """,
        entity_id,
        revision,
        AGENTS,
        agent,
    )
    if row is None:
        raise NoApprovedVersion(f"{agent} has no version {entity_id}/{revision}")
    return await _version(connection, Fact(**dict(row)))


async def _version(connection: Connection, fact: Fact) -> AgentVersion:
    package, action = fact.payload["deliverable"].split(".")
    return AgentVersion(
        agent=fact.payload["agent"],
        entity_id=fact.entity_id,
        revision=fact.revision,
        digest=(await get_proposal(connection, fact.proposal_id)).digest,
        instructions=fact.payload["instructions"],
        memory_instructions=fact.payload["memory_instructions"],
        tools=tuple(fact.payload.get("tools", ())),
        recall_tags=tuple(fact.payload["recall_tags"]),
        schedule=fact.payload["schedule"],
        answer_deadline_hours=fact.payload["answer_deadline_hours"],
        max_rounds=fact.payload["max_rounds"],
        thread=fact.payload["thread"],
        deliverable=(package, action),
    )


async def propose_version(
    pool: asyncpg.Pool,
    registry: Registry,
    payload: dict[str, Any],
    *,
    author: str,
    evidence: Evidence,
) -> Proposal:
    """Propose ``payload`` as ``author``: a change of its agent's version in force, or the
    agent's first version. The agent reads its own definitions, so the author need not."""
    async with session(pool, payload["agent"]) as connection:
        try:
            base: AgentVersion | None = await approved_version(connection, payload["agent"])
        except NoApprovedVersion:
            base = None
    async with session(pool, author) as connection:
        return await propose(
            connection,
            registry.get(AGENTS),
            "publish_agent_version",
            payload=payload,
            evidence=evidence,
            valid_from=datetime.now(UTC),
            entity_id=None if base is None else base.entity_id,
            base_revision=0 if base is None else base.revision,
        )


@dataclass(frozen=True)
class AgentRun:
    id: UUID
    agent: str
    definition: UUID
    revision: int
    context_ref: str
    started_at: datetime
    cycle: str | None = None
    round: int | None = None
    output: dict[str, Any] | None = None
    """What the round answered: its questions or its deliverable."""
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: Decimal | None = None


async def record_run(connection: Connection, version: AgentVersion, context_ref: str) -> AgentRun:
    """Record that the session's agent ran ``version`` on ``context_ref``."""
    row = await connection.fetchrow(
        """
        INSERT INTO pxos.agent_runs (agent, definition, revision, context_ref)
        VALUES ('', $1, $2, $3) RETURNING *
        """,
        version.entity_id,
        version.revision,
        context_ref,
    )
    assert row is not None
    return AgentRun(**dict(row))


async def recorded_run(connection: Connection, context_ref: str) -> AgentRun | None:
    """The run on ``context_ref`` the session can read, or None."""
    row = await connection.fetchrow(
        "SELECT * FROM pxos.agent_runs WHERE context_ref = $1", context_ref
    )
    return None if row is None else AgentRun(**dict(row))
