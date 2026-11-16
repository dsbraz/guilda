"""An agent's definition is a version approved through the proposal cycle (PXOS-78, K34).

ADR 0004 §3: every definition is a published, immutable version with a digest, approved
by a role. The kernel carries it as a canonical type of its own package, so a draft is a
pending proposal, a version is a revision of the agent's entity, and the platform-admin
role approves. Each test runs on a fresh installation, because definitions accumulate.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from conftest import installation

from db_testing import Database
from model_testing import ROUTE
from pxos_kernel.agents import (
    AGENTS,
    AmbiguousDefinition,
    NoApprovedVersion,
    approved_version,
)
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import (
    Evidence,
    InvalidProposal,
    NotApprover,
    Proposal,
    SelfApproval,
    create_pool,
    history,
    propose,
    review,
)

AGENT = "restock-agent"


def definition(instructions: str = "Read each restock promise.") -> dict[str, Any]:
    return {
        "agent": AGENT,
        "instructions": instructions,
        "memory_instructions": "Extract every restock promise, one per fact.",
    }


@pytest.fixture
def fresh(registry: Registry) -> Iterator[Database]:
    yield installation("pxos_test_agent_versions", registry, ROUTE)


@pytest.fixture
async def agents_pool(fresh: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(fresh.app_dsn)
    yield pool
    await pool.close()


async def proposed(
    pool: asyncpg.Pool,
    registry: Registry,
    payload: dict[str, Any],
    *,
    author: str = "frank",
    change: Proposal | None = None,
    revision: int = 0,
) -> Proposal:
    async with session(pool, author) as connection:
        return await propose(
            connection,
            registry.get(AGENTS),
            "publish_agent_version",
            payload=payload,
            evidence=Evidence(
                source=f"test:{author}", quote="Nova versão.", context_ref=f"test:{uuid4()}"
            ),
            valid_from=datetime.now(UTC),
            entity_id=None if change is None else change.entity_id,
            base_revision=revision,
        )


async def decided(
    pool: asyncpg.Pool,
    registry: Registry,
    proposal: Proposal,
    decision: str = "approved",
    *,
    reviewer: str = "paula",
) -> None:
    async with session(pool, reviewer) as connection:
        await review(
            connection,
            registry,
            proposal.id,
            decision,  # type: ignore[arg-type]
            digest=proposal.digest,
            reason="ainda não" if decision == "rejected" else None,
        )


async def in_force(pool: asyncpg.Pool) -> Any:
    async with session(pool, AGENT) as connection:
        return await approved_version(connection, AGENT)


async def test_only_a_platform_admin_approves_a_version(
    agents_pool: asyncpg.Pool, registry: Registry
) -> None:
    draft = await proposed(agents_pool, registry, definition())
    with pytest.raises(NotApprover):  # the agent reads its definitions but approves none
        await decided(agents_pool, registry, draft, reviewer=AGENT)
    with pytest.raises(InvalidProposal):  # a store manager does not even see the draft
        await decided(agents_pool, registry, draft, reviewer="carla")
    own = await proposed(agents_pool, registry, definition(), author="paula")
    with pytest.raises(SelfApproval):
        await decided(agents_pool, registry, own)
    await decided(agents_pool, registry, draft)


async def test_the_agent_runs_only_the_approved_version_in_force(
    agents_pool: asyncpg.Pool, registry: Registry
) -> None:
    with pytest.raises(NoApprovedVersion):
        await in_force(agents_pool)
    first = await proposed(agents_pool, registry, definition("v1"))
    with pytest.raises(NoApprovedVersion):  # a draft is not a version
        await in_force(agents_pool)
    await decided(agents_pool, registry, first)
    version = await in_force(agents_pool)
    assert (version.revision, version.instructions) == (1, "v1")

    rejected = await proposed(agents_pool, registry, definition("v2"), change=first, revision=1)
    await decided(agents_pool, registry, rejected, "rejected")
    assert (await in_force(agents_pool)).instructions == "v1"

    second = await proposed(agents_pool, registry, definition("v3"), change=first, revision=1)
    await decided(agents_pool, registry, second)
    version = await in_force(agents_pool)
    assert (version.revision, version.instructions) == (2, "v3")
    assert version.digest == second.digest
    async with session(agents_pool, AGENT) as connection:
        assert [f.payload["instructions"] for f in await history(connection, first.entity_id)] == [
            "v1",
            "v3",
        ]


async def test_only_platform_admins_and_agents_read_definitions(
    agents_pool: asyncpg.Pool, registry: Registry
) -> None:
    draft = await proposed(agents_pool, registry, definition())
    await decided(agents_pool, registry, draft)
    count = "SELECT count(*) FROM pxos.facts WHERE package = 'pxos_agents'"
    for actor, visible in (("erin", 0), ("carla", 0), ("paula", 1), (AGENT, 1)):
        async with session(agents_pool, actor) as connection:
            assert await connection.fetchval(count) == visible, actor


async def test_two_definitions_of_one_agent_run_neither(
    agents_pool: asyncpg.Pool, registry: Registry
) -> None:
    for text in ("a", "b"):
        draft = await proposed(agents_pool, registry, definition(text))
        await decided(agents_pool, registry, draft)
    with pytest.raises(AmbiguousDefinition):
        await in_force(agents_pool)
