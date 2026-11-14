"""Agent definitions as approved versions (PXOS-78, K34; ADR 0004 §3).

A definition is a canonical type of the kernel's own package, so it goes through the same
proposal cycle as any fact: a draft is a pending proposal, platform-admin approves, and
each approved revision of the agent's entity is an immutable version. Only the version
in force runs.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pxos_kernel.agents.package import generated
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection
from pxos_kernel.ontology import RegisteredPackage, Registry
from pxos_kernel.state import current, get_proposal

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
    proposes: tuple[str, str]
    """The package and action the agent proposes."""


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
    fact = facts[0]
    package, action = fact.payload["proposes"].split(".")
    return AgentVersion(
        agent=agent,
        entity_id=fact.entity_id,
        revision=fact.revision,
        digest=(await get_proposal(connection, fact.proposal_id)).digest,
        instructions=fact.payload["instructions"],
        memory_instructions=fact.payload["memory_instructions"],
        proposes=(package, action),
    )


@dataclass(frozen=True)
class AgentRun:
    id: UUID
    agent: str
    definition: UUID
    revision: int
    context_ref: str
    started_at: datetime


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
