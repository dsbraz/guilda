"""Workflow definitions (ADR 0004). Deterministic code only: no I/O, no clock, no kernel
imports. Temporal replays this code from history, so everything that touches the world
runs in Activities, and what goes through history is references, never content.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

KERNEL_CALL_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=30))
SYNC_TIMEOUT = timedelta(hours=1)
"""A pass waits for the context memory to extract facts from each source, minutes apiece.
An attempt cut short keeps what it committed, so the retry carries on from there."""


def sync_workflow_id(connector: str) -> str:
    """One pass per connector at a time: starting it again while it runs is refused."""
    return f"pxos-sync-{connector}"


@workflow.defn
class SyncConnector:
    """One sync pass of a connector, by name; false when it could not be reached."""

    @workflow.run
    async def run(self, connector: str) -> bool:
        synced: bool = await workflow.execute_activity(
            "sync_connector",
            connector,
            result_type=bool,
            start_to_close_timeout=SYNC_TIMEOUT,
            retry_policy=KERNEL_CALL_RETRY,
        )
        return synced
