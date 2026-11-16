"""Workflow definitions (ADR 0004). Deterministic code only: no I/O, no clock, no kernel
imports. Temporal replays this code from history, so everything that touches the world
runs in Activities, and what goes through history is references, never content.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

KERNEL_CALL_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=30))
INGEST_TIMEOUT = timedelta(minutes=10)
"""Ingestion waits for the context memory to extract facts, which can take minutes."""


def ingest_workflow_id(ref: str) -> str:
    """One workflow per source version: starting it again while it runs is refused."""
    return f"pxos-ingest-{ref}"


@workflow.defn
class IngestSource:
    """Ingest one source version, by reference; true when it was new."""

    @workflow.run
    async def run(self, ref: str) -> bool:
        ingested: bool = await workflow.execute_activity(
            "ingest_source",
            ref,
            result_type=bool,
            start_to_close_timeout=INGEST_TIMEOUT,
            retry_policy=KERNEL_CALL_RETRY,
        )
        return ingested
