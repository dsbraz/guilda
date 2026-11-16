"""Workflow definitions (ADR 0004). Deterministic code only: no I/O, no clock, no kernel
imports. Temporal replays this code from history, so everything that touches the world
runs in Activities, and what goes through history is references, never content.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

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


ROUND_TIMEOUT = timedelta(minutes=10)
"""A round asks the model, which may call the recall tool a few times."""
KERNEL_CALL_TIMEOUT = timedelta(minutes=1)


def schedule_id(agent: str) -> str:
    """The schedule of ``agent``'s cycles; each run it starts gets this id followed by the
    scheduled time, so a scheduled cycle has the id ``cycle_workflow_id`` gives it."""
    return f"pxos-cycle-{agent}"


def cycle_workflow_id(agent: str, cycle: str) -> str:
    """The workflow of ``agent``'s ``cycle``; the id is also the cycle's reference in the
    conversation and the runs, and a cycle that completed never runs again."""
    return f"{schedule_id(agent)}-{cycle}"


@dataclass(frozen=True)
class Pinned:
    """The version a cycle pinned at its start, and the parameters its steps follow."""

    entity_id: str
    revision: int
    max_rounds: int
    answer_deadline_seconds: float


@dataclass(frozen=True)
class Round:
    """A round of a cycle, by reference: the version it runs is the one the cycle pinned."""

    agent: str
    cycle: str
    entity_id: str
    revision: int
    round: int
    final: bool


@workflow.defn
class AgentCycle:
    """The agent's cycle, the same steps for every agent; its published version sets the
    parameters. It syncs the connectors and runs rounds: questions go to the thread and the
    cycle waits for an answer until the deadline, a deliverable is proposed and the cycle
    waits for its review. Approved ends the cycle; rejected runs another round, with the
    reason as a correction, up to the version's maximum.

    Signals only wake it: what the team said is read from the database by the round."""

    def __init__(self) -> None:
        self._answers = 0
        self._decisions: dict[str, str] = {}

    @workflow.signal
    def answered(self) -> None:
        self._answers += 1

    @workflow.signal
    def reviewed(self, proposal_id: str, decision: str) -> None:
        self._decisions[proposal_id] = decision

    @workflow.run
    async def run(self, agent: str) -> str:
        """Returns the decision on the cycle's last deliverable."""
        cycle = workflow.info().workflow_id
        pinned: Pinned = await _call("pin_version", agent, Pinned)
        await workflow.execute_activity(
            "sync_connectors",
            agent,
            start_to_close_timeout=SYNC_TIMEOUT,
            retry_policy=KERNEL_CALL_RETRY,
        )
        decision = "rejected"
        for number in range(1, pinned.max_rounds + 1):
            step = Round(
                agent, cycle, pinned.entity_id, pinned.revision, number, number == pinned.max_rounds
            )
            if await _call("run_round", step, str, ROUND_TIMEOUT) == "questions":
                await self._ask(step, pinned.answer_deadline_seconds)
                continue
            decision = await self._deliver(step)
            if decision == "approved":
                break
        return decision

    async def _ask(self, step: Round, deadline: float) -> None:
        seen = self._answers
        await _call("post_questions", step, None)
        with suppress(asyncio.TimeoutError):
            await workflow.wait_condition(
                lambda: self._answers > seen, timeout=timedelta(seconds=deadline)
            )

    async def _deliver(self, step: Round) -> str:
        proposal: str = await _call("propose_deliverable", step, str)
        await workflow.wait_condition(lambda: proposal in self._decisions)
        return self._decisions[proposal]


async def _call(
    activity: str, argument: Any, result: type | None, timeout: timedelta = KERNEL_CALL_TIMEOUT
) -> Any:
    return await workflow.execute_activity(
        activity,
        argument,
        result_type=result,
        start_to_close_timeout=timeout,
        retry_policy=KERNEL_CALL_RETRY,
    )
