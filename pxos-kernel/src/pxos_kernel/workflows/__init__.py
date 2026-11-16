"""Durable processes on Temporal, bound to the kernel (ADR 0004)."""

from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from pxos_kernel.workflows.activities import IngestionActivities
from pxos_kernel.workflows.definitions import IngestSource, ingest_workflow_id


def workflow_runner() -> SandboxedWorkflowRunner:
    """The sandbox for kernel workflows. The kernel package is passed through: workflow code
    imports only its definitions, and re-importing the kernel's database and HTTP libraries
    inside the sandbox fails."""
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("pxos_kernel")
    )


__all__ = [
    "IngestSource",
    "IngestionActivities",
    "ingest_workflow_id",
    "workflow_runner",
]
