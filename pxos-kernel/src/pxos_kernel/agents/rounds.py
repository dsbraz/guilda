"""Rounds of an agent's cycle and the deliverable they propose (ADR 0006 §2, §3).

Each round of a cycle is recorded once as a run of the version, as the agent, with what it
answered and the model, tokens and cost the gateway counted; a retry gets the recorded
answer and asks the model nothing. The deliverable is a proposal of the version's
deliverable action, by the agent, whose evidence points to the run and the round; a
person with the action's approver role reviews it. The database takes one per run.
"""

from datetime import UTC, datetime
from typing import Any

import asyncpg

from pxos_kernel.agents.runtime import Agent, RoundInput, RoundOutput
from pxos_kernel.agents.versions import AgentRun, AgentVersion
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection, session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Evidence, Proposal, propose


class NoRecordedRun(PxosError):
    pass


def round_ref(cycle: str, round: int) -> str:
    return f"{cycle}/round:{round}"


async def recorded_round(connection: Connection, cycle: str, round: int) -> AgentRun | None:
    """The session agent's run of ``round`` in ``cycle``, or None."""
    row = await connection.fetchrow(
        "SELECT * FROM pxos.agent_runs WHERE cycle = $1 AND round = $2", cycle, round
    )
    return None if row is None else AgentRun(**dict(row))


def answered(run: AgentRun) -> RoundOutput:
    output: dict[str, Any] = run.output or {}
    return RoundOutput(
        questions=tuple(output.get("questions", ())), deliverable=output.get("deliverable")
    )


async def run_round(
    pool: asyncpg.Pool, agent: Agent, round_input: RoundInput, *, cycle: str, round: int
) -> RoundOutput:
    """Run ``round`` of ``cycle`` once; a retry returns what the recorded run answered.

    The run is recorded only after a valid answer, so a refused answer leaves no trace.
    """
    version = agent.version
    async with session(pool, version.agent) as connection:
        run = await recorded_round(connection, cycle, round)
    if run is not None:
        return answered(run)
    output = await agent.answer(round_input)
    usage = agent.usage()
    recorded = (
        {"questions": list(output.questions)}
        if output.deliverable is None
        else {"deliverable": output.deliverable}
    )
    async with session(pool, version.agent) as connection:
        await connection.execute(
            """
            INSERT INTO pxos.agent_runs (agent, definition, revision, context_ref, cycle, round,
                output, model, input_tokens, output_tokens, cost)
            VALUES ('', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            version.entity_id,
            version.revision,
            round_ref(cycle, round),
            cycle,
            round,
            recorded,
            usage.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cost,
        )
    return output


async def propose_deliverable(
    pool: asyncpg.Pool, registry: Registry, version: AgentVersion, *, cycle: str, round: int
) -> Proposal:
    """Propose, as the agent, the deliverable ``round`` of ``cycle`` answered; proposing it
    again returns the same proposal."""
    package, action = version.deliverable
    async with session(pool, version.agent) as connection:
        run = await recorded_round(connection, cycle, round)
        deliverable = None if run is None else answered(run).deliverable
        if run is None or deliverable is None:
            raise NoRecordedRun(f"no run of {version.agent} delivered {round_ref(cycle, round)}")
        source = f"agent-run:{run.id}"
        row = await connection.fetchrow(
            "SELECT * FROM pxos.proposals WHERE evidence->>'source' = $1", source
        )
        if row is not None:
            return Proposal(**dict(row))
        return await propose(
            connection,
            registry.get(package),
            action,
            payload=deliverable,
            evidence=Evidence(
                source=source,
                quote=f"{version.agent}@{version.revision}",
                context_ref=run.context_ref,
            ),
            valid_from=datetime.now(UTC),
        )
