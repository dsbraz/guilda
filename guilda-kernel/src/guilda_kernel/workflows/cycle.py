"""Starting, scheduling and waking the agent's cycle (ADR 0006 §1, §2). Runs outside
workflows.

The kernel's outbox publisher composes, per event, what the cycle needs: a review card
for each proposal; a team's answer retained in the version's recall scope, which wakes
the cycle; a rejection's reason retained the same way, which wakes the cycle with the
decision, as an approval does; and each new version of the agent creates or updates the
Temporal Schedule of its cycles. Events of anything else, and of cycles that are over,
pass through, so they never hold the outbox back.
"""

from typing import Any
from uuid import UUID

import asyncpg
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
    WorkflowHandle,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from guilda_kernel.agents import (
    AGENTS,
    AgentVersion,
    NoApprovedVersion,
    approved_version,
    pinned_version,
)
from guilda_kernel.channel import Channel, route_cards
from guilda_kernel.context import ContextStore
from guilda_kernel.conversation import retain_message, retain_rejection
from guilda_kernel.identity import session
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Event, Handler
from guilda_kernel.workflows.definitions import AgentCycle, cycle_workflow_id, schedule_id


async def start_cycle(
    client: Client, agent: str, cycle: str, *, task_queue: str
) -> WorkflowHandle[AgentCycle, str]:
    """Start ``agent``'s ``cycle``; refused while it runs and once it completed, so the same
    cycle never runs twice. Only a cycle that failed may start again, and its rounds,
    posts and deliverables already recorded are not repeated."""
    return await client.start_workflow(
        AgentCycle.run,
        agent,
        id=cycle_workflow_id(agent, cycle),
        task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
    )


async def schedule_cycles(client: Client, version: AgentVersion, *, task_queue: str) -> None:
    """Create the Schedule of ``version``'s agent from its cron, in UTC, or update it."""
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            AgentCycle.run, version.agent, id=schedule_id(version.agent), task_queue=task_queue
        ),
        spec=ScheduleSpec(cron_expressions=[version.schedule]),
    )

    def replace(_: ScheduleUpdateInput) -> ScheduleUpdate:
        return ScheduleUpdate(schedule=schedule)

    try:
        await client.create_schedule(schedule_id(version.agent), schedule)
    except ScheduleAlreadyRunningError:
        await client.get_schedule_handle(schedule_id(version.agent)).update(replace)


def cycle_handlers(
    pool: asyncpg.Pool,
    registry: Registry,
    store: ContextStore,
    channel: Channel,
    client: Client,
    *,
    agent: str,
    task_queue: str,
) -> dict[str, Handler]:
    """The outbox handlers of ``agent``'s cycles."""

    async def version() -> AgentVersion:
        async with session(pool, agent) as connection:
            return await approved_version(connection, agent)

    async def answered(event: Event) -> None:
        cycle = event.payload["cycle"]
        if not cycle.startswith(f"{schedule_id(agent)}-"):
            return  # a conversation of no cycle of this agent
        scope = await version()
        message = UUID(event.payload["message_id"])
        await retain_message(
            pool, store, agent, message, scope.memory_instructions, tags=scope.recall_tags
        )
        await _signal(client, cycle, AgentCycle.answered)

    async def rejected(event: Event) -> None:
        proposal = UUID(event.payload["proposal_id"])
        cycle = await _cycle_of(pool, agent, proposal)
        if cycle is None:
            return
        scope = await version()
        await retain_rejection(
            pool, store, agent, proposal, scope.memory_instructions, tags=scope.recall_tags
        )
        await _signal(client, cycle, AgentCycle.reviewed, str(proposal), "rejected")

    async def recorded(event: Event) -> None:
        if event.payload["package"] == AGENTS:
            await _schedule(pool, client, agent, event, task_queue=task_queue)
            return
        proposal = UUID(event.payload["proposal_id"])
        cycle = await _cycle_of(pool, agent, proposal)
        if cycle is not None:
            await _signal(client, cycle, AgentCycle.reviewed, str(proposal), "approved")

    return {
        "proposal.created": route_cards(pool, registry, channel),
        "message.received": answered,
        "proposal.rejected": rejected,
        "fact.recorded": recorded,
    }


async def _schedule(
    pool: asyncpg.Pool, client: Client, agent: str, event: Event, *, task_queue: str
) -> None:
    """Schedule ``agent``'s cycles from the version ``event`` recorded, if it is one of its."""
    try:
        async with session(pool, agent) as connection:
            published = await pinned_version(
                connection, agent, event.entity_id, event.payload["revision"]
            )
    except NoApprovedVersion:
        return  # a version of another agent
    await schedule_cycles(client, published, task_queue=task_queue)


async def _cycle_of(pool: asyncpg.Pool, agent: str, proposal: UUID) -> str | None:
    """The cycle whose round proposed ``proposal``, if one of ``agent``'s did."""
    async with session(pool, agent) as connection:
        cycle: str | None = await connection.fetchval(
            """
            SELECT a.cycle FROM pxos.proposals p
            JOIN pxos.agent_runs a ON p.evidence->>'source' = 'agent-run:' || a.id
            WHERE p.id = $1
            """,
            proposal,
        )
        return cycle


async def _signal(client: Client, cycle: str, signal: Any, *args: str) -> None:
    """Wake ``cycle``; a cycle that is over has nothing left to wake."""
    try:
        await client.get_workflow_handle(cycle).signal(signal, args=list(args))
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
