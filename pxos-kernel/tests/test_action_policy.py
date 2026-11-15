"""Action with policy in the database (PXOS-83, PXOS-84; ADR 0003 §2).

Refusals are sent as raw SQL by the application login: a pass means the database
refused, whatever the Python policy decided.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from db_testing import Database
from pxos_kernel.identity import install_identities, read_identities, session
from pxos_kernel.ontology import Registry
from pxos_kernel.policy import install_policy, read_policy
from pxos_kernel.state import (
    DecisionFinal,
    Evidence,
    NotExecutable,
    PersonRequired,
    Proposal,
    execute,
    propose,
    review,
)

RETAIL = Path(__file__).parent / "retail_fixture"

WHEN = datetime(2026, 11, 14, tzinfo=UTC)
EVIDENCE = Evidence(source="mcp:planner-agent", quote="faltam 3", context_ref="mcp:call/1")


def order(quantity: int = 3) -> dict[str, Any]:
    return {"sku": "P-1", "quantity": quantity, "occurred_at": "2026-11-14T09:00:00Z"}


async def proposed(
    pool: asyncpg.Pool,
    registry: Registry,
    agent: str,
    *,
    action: str = "create_restock_order",
    quantity: int = 3,
    person: str | None = "erin",
) -> Proposal:
    async with session(pool, agent, person) as connection:
        return await propose(
            connection,
            registry.get("retail"),
            action,
            payload=order(quantity),
            evidence=EVIDENCE,
            valid_from=WHEN,
        )


async def raw_execution(
    pool: asyncpg.Pool, proposal: Proposal, actor: str, person: str | None = "erin"
) -> None:
    async with session(pool, actor, person) as connection:
        await connection.execute(
            "INSERT INTO pxos.executions (proposal_id, proposal_digest, executor)"
            " VALUES ($1, $2, 'night-agent')",
            proposal.id,
            proposal.digest,
        )


async def test_autonomy_comes_from_the_installation_and_a_redeploy_lowers_it(
    database: Database, tmp_path: Path
) -> None:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        rows = await owner.fetch("SELECT id, autonomy FROM pxos.actors")
        phases = {row["id"]: row["autonomy"] for row in rows}
        assert (phases["restock-agent"], phases["planner-agent"], phases["night-agent"]) == (
            1,
            2,
            3,
        )
        assert phases["erin"] == 1

        lowered = tmp_path / "identities.yaml"
        content = (RETAIL / "identities.yaml").read_text(encoding="utf-8")
        lowered.write_text(content.replace("autonomy: 3", "autonomy: 1"), encoding="utf-8")
        await install_identities(owner, read_identities(lowered))
        assert (
            await owner.fetchval("SELECT autonomy FROM pxos.actors WHERE id = 'night-agent'") == 1
        )
    finally:
        await install_identities(owner, read_identities(RETAIL / "identities.yaml"))
        await owner.close()


async def test_the_application_cannot_change_autonomy(pool: asyncpg.Pool) -> None:
    async with session(pool, "night-agent", "erin") as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                "UPDATE pxos.actors SET autonomy = 3 WHERE id = 'restock-agent'"
            )


async def test_an_allowed_execution_records_the_fact_without_a_card(
    pool: asyncpg.Pool, registry: Registry, database: Database
) -> None:
    proposal = await proposed(pool, registry, "planner-agent")
    async with session(pool, "planner-agent", "erin") as connection:
        await execute(connection, proposal)
        await execute(connection, proposal)  # a redelivery is the same execution
        facts = await connection.fetch(
            "SELECT revision FROM pxos.facts WHERE proposal_id = $1", proposal.id
        )
    assert [row["revision"] for row in facts] == [1]

    owner = await asyncpg.connect(database.owner_dsn)
    try:
        events = await owner.fetch(
            "SELECT event_type FROM pxos.outbox WHERE payload->>'proposal_id' = $1",
            str(proposal.id),
        )
    finally:
        await owner.close()
    assert [row["event_type"] for row in events] == ["fact.recorded"]


@pytest.mark.parametrize(
    ("author", "executor", "person", "action", "quantity"),
    [
        ("restock-agent", "restock-agent", "erin", "create_restock_order", 3),  # phase 1
        ("planner-agent", "planner-agent", "erin", "create_restock_order", 11),  # outside rule
        ("night-agent", "night-agent", "erin", "create_restock_order", 11),  # rules bind phase 3
        ("night-agent", "night-agent", "erin", "confirm_restock_order", 3),  # irreversible
        ("planner-agent", "night-agent", "erin", "create_restock_order", 3),  # not the author
        ("erin", "erin", None, "create_restock_order", 3),  # a person
    ],
    ids=["phase-1", "outside-rule", "phase-3-outside-rule", "irreversible", "other", "person"],
)
async def test_the_database_refuses_a_direct_execution_the_policy_forbids(
    pool: asyncpg.Pool,
    registry: Registry,
    author: str,
    executor: str,
    person: str | None,
    action: str,
    quantity: int,
) -> None:
    author_person = None if author == "erin" else "erin"
    proposal = await proposed(
        pool, registry, author, action=action, quantity=quantity, person=author_person
    )
    with pytest.raises(asyncpg.PostgresError) as error:
        await raw_execution(pool, proposal, executor, person)
    assert error.value.sqlstate == "PX016"

    async with session(pool, executor, person) as connection:
        with pytest.raises(NotExecutable):
            await execute(connection, proposal)


async def test_phase_2_needs_declared_rules_and_phase_3_does_not(
    pool: asyncpg.Pool, registry: Registry, database: Database
) -> None:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        await owner.execute("DELETE FROM pxos.policy_rules WHERE action = 'create_restock_order'")
        planned = await proposed(pool, registry, "planner-agent", quantity=50)
        with pytest.raises(asyncpg.PostgresError) as error:
            await raw_execution(pool, planned, "planner-agent")
        assert error.value.sqlstate == "PX016"

        night = await proposed(pool, registry, "night-agent", quantity=50)
        await raw_execution(pool, night, "night-agent")
    finally:
        await install_policy(owner, read_policy([RETAIL / "policy.yaml"], registry))
        await owner.close()


async def test_a_proposal_is_decided_once(pool: asyncpg.Pool, registry: Registry) -> None:
    executed = await proposed(pool, registry, "planner-agent")
    await raw_execution(pool, executed, "planner-agent")
    async with session(pool, "carla") as connection:
        with pytest.raises(asyncpg.PostgresError) as error:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO pxos.reviews (proposal_id, decision, proposal_digest)"
                    " VALUES ($1, 'approved', $2)",
                    executed.id,
                    executed.digest,
                )
        assert error.value.sqlstate == "PX017"
        with pytest.raises(DecisionFinal):
            await review(connection, registry, executed.id, "approved", digest=executed.digest)

    reviewed = await proposed(pool, registry, "planner-agent")
    async with session(pool, "carla") as connection:
        await review(
            connection, registry, reviewed.id, "rejected", digest=reviewed.digest, reason="não"
        )
    with pytest.raises(asyncpg.PostgresError) as error:
        await raw_execution(pool, reviewed, "planner-agent")
    assert error.value.sqlstate == "PX017"


async def test_concurrent_redelivery_records_one_fact(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    proposal = await proposed(pool, registry, "planner-agent")

    async def deliver() -> None:
        async with session(pool, "planner-agent", "erin") as connection:
            await execute(connection, proposal)

    await asyncio.gather(*(deliver() for _ in range(4)))
    async with session(pool, "erin") as connection:
        count = await connection.fetchval(
            "SELECT count(*) FROM pxos.facts WHERE proposal_id = $1", proposal.id
        )
    assert count == 1


@pytest.mark.parametrize(
    ("reviewer", "person", "sqlstate"),
    [("restock-agent", "carla", "PX018"), ("carla", None, None)],
    ids=["agent-for-approver", "the-person"],
)
async def test_an_irreversible_action_is_approved_only_by_the_person(
    pool: asyncpg.Pool, registry: Registry, reviewer: str, person: str | None, sqlstate: str | None
) -> None:
    proposal = await proposed(pool, registry, "night-agent", action="confirm_restock_order")
    async with session(pool, reviewer, person) as connection:
        if sqlstate is None:
            await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
            return
        with pytest.raises(asyncpg.PostgresError) as error:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO pxos.reviews (proposal_id, decision, proposal_digest)"
                    " VALUES ($1, 'approved', $2)",
                    proposal.id,
                    proposal.digest,
                )
        assert error.value.sqlstate == sqlstate
        with pytest.raises(PersonRequired):
            await review(connection, registry, proposal.id, "approved", digest=proposal.digest)


async def test_audit_is_append_only_and_its_actor_comes_from_the_session(
    pool: asyncpg.Pool,
) -> None:
    async with session(pool, "planner-agent", "erin") as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO pxos.action_audit (actor, principal, package, action, decision, reason,
                                           result)
            VALUES ('carla', 'carla', 'retail', 'create_restock_order', 'deny', 'teste',
                    'denied')
            RETURNING actor, principal
            """
        )
        assert row is not None and (row["actor"], row["principal"]) == ("planner-agent", "erin")
    for statement in ["UPDATE pxos.action_audit SET reason = 'x'", "DELETE FROM pxos.action_audit"]:
        async with session(pool, "planner-agent", "erin") as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)


async def test_the_application_cannot_change_policy_rules(pool: asyncpg.Pool) -> None:
    for statement in [
        "UPDATE pxos.policy_rules SET value = 1000",
        "DELETE FROM pxos.policy_rules",
        "INSERT INTO pxos.policy_rules (package, action, position, kind, effect, roles)"
        " VALUES ('retail', 'create_restock_order', 9, 'roles', 'deny', '{agent}')",
    ]:
        async with session(pool, "planner-agent", "erin") as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)


async def test_a_new_limit_applies_to_open_proposals(
    pool: asyncpg.Pool, registry: Registry, database: Database, tmp_path: Path
) -> None:
    """Limits are deploy configuration, not part of the package digest (PXOS-87)."""
    proposal = await proposed(pool, registry, "planner-agent", quantity=20)
    with pytest.raises(asyncpg.PostgresError):
        await raw_execution(pool, proposal, "planner-agent")

    deploy = tmp_path / "deploy.yaml"
    deploy.write_text(
        "actions:\n  retail.create_restock_order:\n"
        "    - {parameter: quantity, max: 50, otherwise: approval}\n",
        encoding="utf-8",
    )
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        await install_policy(owner, read_policy([RETAIL / "policy.yaml", deploy], registry))
        await raw_execution(pool, proposal, "planner-agent")
    finally:
        await install_policy(owner, read_policy([RETAIL / "policy.yaml"], registry))
        await owner.close()
