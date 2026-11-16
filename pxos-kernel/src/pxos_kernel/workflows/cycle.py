"""Starting the agent's cycle (ADR 0006 §2). Runs outside workflows."""

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy

from pxos_kernel.workflows.definitions import AgentCycle, cycle_workflow_id


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
