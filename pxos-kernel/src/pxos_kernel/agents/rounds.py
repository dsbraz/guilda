"""Rounds of an agent's cycle and the deliverable they propose (ADR 0006 §2, §3).

Each round is recorded as a run of the version, once per round reference, as the agent.
The deliverable is a proposal of the version's deliverable action, by the agent, whose
evidence points to the run and the round; a person with the action's approver role
reviews it.
"""

from datetime import UTC, datetime
from typing import Any

import asyncpg

from pxos_kernel.agents.runtime import Agent, RoundInput, RoundOutput
from pxos_kernel.agents.versions import AgentVersion, record_run, recorded_run
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Evidence, Proposal, propose


class NoRecordedRun(PxosError):
    pass


async def run_round(
    pool: asyncpg.Pool, agent: Agent, round_input: RoundInput, *, context_ref: str
) -> RoundOutput:
    """Run one round and record it under ``context_ref``; a replay records nothing new.

    The run is recorded only after a valid answer, so a refused answer leaves no trace.
    """
    output = await agent.answer(round_input)
    async with session(pool, agent.version.agent) as connection:
        if await recorded_run(connection, context_ref) is None:
            await record_run(connection, agent.version, context_ref)
    return output


async def propose_deliverable(
    pool: asyncpg.Pool,
    registry: Registry,
    version: AgentVersion,
    payload: dict[str, Any],
    *,
    context_ref: str,
) -> Proposal:
    """Propose ``payload`` as the agent, from the run recorded under ``context_ref``."""
    package, action = version.deliverable
    async with session(pool, version.agent) as connection:
        run = await recorded_run(connection, context_ref)
        if run is None:
            raise NoRecordedRun(f"no run of {version.agent} on {context_ref}")
        return await propose(
            connection,
            registry.get(package),
            action,
            payload=payload,
            evidence=Evidence(
                source=f"agent-run:{run.id}",
                quote=f"{version.agent}@{version.revision}",
                context_ref=context_ref,
            ),
            valid_from=datetime.now(UTC),
        )
