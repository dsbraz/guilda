"""Workflow definitions (ADR 0004). Deterministic code only: no I/O, no clock, no kernel
imports. Temporal replays this code from history, so everything that touches the world
runs in Activities, and what goes through history is references, never content.
"""

import asyncio
from datetime import timedelta
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

RECHECK = timedelta(hours=1)
"""A lost signal costs at most this long: the workflow reads the kernel again anyway."""

KERNEL_CALL_TIMEOUT = timedelta(seconds=30)
KERNEL_CALL_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=30))
INGEST_TIMEOUT = timedelta(minutes=10)
"""Ingestion waits for the context memory to extract facts, which can take minutes."""


def review_workflow_id(proposal_id: UUID | str) -> str:
    return f"pxos-review-{proposal_id}"


def ingest_workflow_id(ref: str) -> str:
    """One workflow per source version: starting it again while it runs is refused."""
    return f"pxos-ingest-{ref}"


@workflow.defn
class AwaitReview:
    """Wait for the decision on one proposal; the kernel, not the signal, decides."""

    def __init__(self) -> None:
        self._nudged = False

    @workflow.signal
    def reviewed(self) -> None:
        self._nudged = True

    @workflow.run
    async def run(self, proposal_id: str) -> str:
        while True:
            self._nudged = False
            decision: str | None = await workflow.execute_activity(
                "decision_of",
                proposal_id,
                result_type=str | None,  # type: ignore[arg-type]
                start_to_close_timeout=KERNEL_CALL_TIMEOUT,
                retry_policy=KERNEL_CALL_RETRY,
            )
            if decision is not None:
                return decision
            try:
                await workflow.wait_condition(lambda: self._nudged, timeout=RECHECK)
            except TimeoutError:
                pass


@workflow.defn
class IngestSource:
    """Ingest one source version, by reference, and wait for the decision on each proposal."""

    @workflow.run
    async def run(self, ref: str) -> dict[str, str]:
        proposal_ids: list[str] = await workflow.execute_activity(
            "ingest_source",
            ref,
            result_type=list[str],
            start_to_close_timeout=INGEST_TIMEOUT,
            retry_policy=KERNEL_CALL_RETRY,
        )
        decisions = await asyncio.gather(
            *(
                workflow.execute_child_workflow(
                    AwaitReview.run, proposal_id, id=review_workflow_id(proposal_id)
                )
                for proposal_id in proposal_ids
            )
        )
        return dict(zip(proposal_ids, decisions, strict=True))
