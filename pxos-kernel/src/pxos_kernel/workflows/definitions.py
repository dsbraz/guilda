"""Workflow definitions (ADR 0004). Deterministic code only: no I/O, no clock, no kernel
imports. Temporal replays this code from history, so everything that touches the world
runs in Activities, and what goes through history is references, never content.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

RECHECK = timedelta(hours=1)
"""A lost signal costs at most this long: the workflow reads the kernel again anyway."""

KERNEL_CALL_TIMEOUT = timedelta(seconds=30)
KERNEL_CALL_RETRY = RetryPolicy(maximum_interval=timedelta(seconds=30))


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
