"""Durable processes on Temporal, bound to the kernel (ADR 0004)."""

from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from guilda_kernel.workflows.activities import CycleActivities, SyncActivities
from guilda_kernel.workflows.cycle import cycle_handlers, schedule_cycles, start_cycle
from guilda_kernel.workflows.definitions import (
    AgentCycle,
    SyncConnector,
    cycle_workflow_id,
    schedule_id,
    sync_workflow_id,
)


def workflow_runner() -> SandboxedWorkflowRunner:
    """The sandbox for kernel workflows. The kernel package is passed through: workflow code
    imports only its definitions, and re-importing the kernel's database and HTTP libraries
    inside the sandbox fails."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("guilda_kernel")
    )


__all__ = [
    "AgentCycle",
    "CycleActivities",
    "SyncActivities",
    "SyncConnector",
    "cycle_handlers",
    "cycle_workflow_id",
    "schedule_cycles",
    "schedule_id",
    "start_cycle",
    "sync_workflow_id",
    "workflow_runner",
]
