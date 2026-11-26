"""Activities: where workflows touch the kernel, always in an actor's database session."""

from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from uuid import UUID

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from guilda_kernel.agents import (
    AgentVersion,
    approved_version,
    build_agent,
    pinned_version,
    post_questions,
    propose_deliverable,
    round_input,
    run_round,
    scoped_recall,
)
from guilda_kernel.channel import Channel
from guilda_kernel.context import ContextStore, ContextUnavailable
from guilda_kernel.identity import session
from guilda_kernel.ingestion import Connector, sync
from guilda_kernel.model_access import ModelGateway
from guilda_kernel.ontology import Registry
from guilda_kernel.state import InvalidProposal, get_review
from guilda_kernel.workflows.definitions import Delivered, Pinned, Round

TRANSIENT = (OSError, TimeoutError, asyncpg.PostgresConnectionError, ContextUnavailable)
"""Failures worth retrying. Anything else fails the workflow, which can be started again
with the same id: a defect or bad data does not fix itself on retry."""


class SyncActivities:
    """Run one sync pass of a connector, by name, as ``agent``.

    Only references cross into the workflow: the connector's name in, a flag out. A
    failure is reported by its type alone, because its message may quote a source,
    whether it came from the connector or from the context memory.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        store: ContextStore,
        connectors: Iterable[Connector],
        *,
        agent: str,
    ) -> None:
        self._pool = pool
        self._store = store
        self._connectors = {connector.name: connector for connector in connectors}
        self._agent = agent

    @activity.defn(name="sync_connector")
    async def sync_connector(self, name: str) -> bool:
        try:
            return await self.sync(self._connectors[name])
        except Exception as error:
            raise ApplicationError(
                f"sync of {name} failed: {type(error).__name__}",
                non_retryable=not isinstance(error, TRANSIENT),
            ) from None

    async def sync(self, connector: Connector) -> bool:
        return await sync(self._pool, self._store, connector, agent=self._agent)

    def registered(self) -> list[Callable[..., Any]]:
        return [self.sync_connector]


async def _reduced[T](name: str, call: Awaitable[T]) -> T:
    """``call``'s result, or its failure reduced to its type."""
    try:
        return await call
    except Exception as error:
        raise ApplicationError(
            f"{name} failed: {type(error).__name__}",
            non_retryable=not isinstance(error, TRANSIENT),
        ) from None


class CycleActivities:
    """The steps of the agent's cycle, each idempotent, in the agent's database session.

    Only references cross into the workflow: the round in, a kind or a proposal id out.
    What the round reads and answers stays in the database, and a failure is reported by
    its type alone.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        store: ContextStore,
        connectors: Iterable[Connector],
        gateway: ModelGateway,
        registry: Registry,
        channel: Channel,
    ) -> None:
        self._pool = pool
        self._store = store
        self._connectors = list(connectors)
        self._gateway = gateway
        self._registry = registry
        self._channel = channel

    @activity.defn(name="pin_version")
    async def pin_version(self, agent: str) -> Pinned:
        return await _reduced("pin_version", self.pin(agent))

    async def pin(self, agent: str) -> Pinned:
        async with session(self._pool, agent) as connection:
            version = await approved_version(connection, agent)
        return Pinned(
            entity_id=str(version.entity_id),
            revision=version.revision,
            max_rounds=version.max_rounds,
            answer_deadline_seconds=version.answer_deadline_hours * 3600.0,
        )

    @activity.defn(name="sync_connectors")
    async def sync_connectors(self, agent: str) -> None:
        await _reduced("sync_connectors", self._sync(agent))

    async def _sync(self, agent: str) -> None:
        for connector in self._connectors:
            await sync(self._pool, self._store, connector, agent=agent)

    @activity.defn(name="run_round")
    async def run_round(self, step: Round) -> str:
        return await _reduced("run_round", self._run(step))

    async def _run(self, step: Round) -> str:
        version = await self._version(step)
        model = await self._gateway.adk_model()
        recall = scoped_recall(self._store, version)
        async with self._pool.acquire() as connection:
            agent = await build_agent(connection, model, self._registry, version, recall=recall)
        data = await round_input(self._pool, step.agent, step.cycle, final_round=step.final)
        output = await run_round(self._pool, agent, data, cycle=step.cycle, round=step.round)
        return "questions" if output.deliverable is None else "deliverable"

    @activity.defn(name="post_questions")
    async def post_questions(self, step: Round) -> None:
        await _reduced("post_questions", self._post(step))

    async def _post(self, step: Round) -> None:
        version = await self._version(step)
        await post_questions(self._pool, self._channel, version, cycle=step.cycle, round=step.round)

    @activity.defn(name="propose_deliverable")
    async def propose_deliverable(self, step: Round) -> Delivered:
        return await _reduced("propose_deliverable", self._propose(step))

    async def _propose(self, step: Round) -> Delivered:
        version = await self._version(step)
        proposal = await propose_deliverable(
            self._pool, self._registry, version, cycle=step.cycle, round=step.round
        )
        async with session(self._pool, step.agent) as connection:
            try:
                decision: str | None = (await get_review(connection, proposal.id)).decision
            except InvalidProposal:  # not reviewed yet
                decision = None
        return Delivered(str(proposal.id), decision)

    async def _version(self, step: Round) -> AgentVersion:
        async with session(self._pool, step.agent) as connection:
            return await pinned_version(connection, step.agent, UUID(step.entity_id), step.revision)

    def registered(self) -> list[Callable[..., Any]]:
        return [
            self.pin_version,
            self.sync_connectors,
            self.run_round,
            self.post_questions,
            self.propose_deliverable,
        ]
