"""Shared helpers for tests that publish agent versions and script the model's answers."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import asyncpg
from google.adk.models.llm_request import LlmRequest

from pxos_kernel.agents import AGENTS, AgentVersion
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Evidence, Proposal, propose, review


async def publish(
    pool: asyncpg.Pool,
    registry: Registry,
    payload: dict[str, Any],
    *,
    author: str,
    approver: str,
    change: AgentVersion | None = None,
) -> Proposal:
    """Propose a version of an agent as ``author`` and approve it as ``approver``."""
    async with session(pool, author) as connection:
        proposal = await propose(
            connection,
            registry.get(AGENTS),
            "publish_agent_version",
            payload=payload,
            evidence=Evidence(
                source=f"test:{author}", quote="Nova versão.", context_ref=f"test:{uuid4()}"
            ),
            valid_from=datetime.now(UTC),
            entity_id=None if change is None else change.entity_id,
            base_revision=0 if change is None else change.revision,
        )
    async with session(pool, approver) as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
    return proposal


def fact_of(request: LlmRequest) -> str:
    """The extracted fact the agent was asked to read: the last user message."""
    content = request.contents[-1]
    return "".join(part.text or "" for part in content.parts or [])


def scripted(read: Callable[[str], dict[str, Any] | None]) -> Callable[[LlmRequest], str]:
    """A model reply that answers each fact with ``read(fact)`` as the agent's JSON output."""

    def reply(request: LlmRequest) -> str:
        return json.dumps(read(fact_of(request)) or {"payload": None, "quote": None})

    return reply
