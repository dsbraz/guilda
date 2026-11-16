"""Agent runs are records the application cannot forge or change (PXOS-79, K34).

The run's agent comes from the session, and the revision must be one of that agent's
approved versions. Only the agent runtime builds ADK agents, so every agent gets its model
from the gateway (ADR 0004 §2, rule 1).
"""

import re
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT

import pxos_kernel
from pxos_kernel.agents import approved_version
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import SourceDocument, ingest


async def test_the_application_cannot_forge_or_change_a_run(pool: asyncpg.Pool) -> None:
    doc = SourceDocument(
        source=f"fixture:shelf-log/{uuid4()}",
        version="1",
        text="Erin: restock A-1.",
        access=(),
    )
    await ingest(pool, FakeContextStore(), doc, agent=AGENT, connector="fixture")
    async with session(pool, AGENT) as connection:
        version = await approved_version(connection, AGENT)
    for statement in (
        "UPDATE pxos.agent_runs SET revision = 1",
        "DELETE FROM pxos.agent_runs",
    ):
        async with session(pool, AGENT) as connection:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)
    forge = "INSERT INTO pxos.agent_runs (definition, revision, context_ref) VALUES ($1, $2, $3)"
    for actor, revision in ((AGENT, version.revision + 100), ("paula", version.revision)):
        async with session(pool, actor) as connection:
            with pytest.raises(asyncpg.PostgresError) as refused:
                await connection.execute(forge, version.entity_id, revision, "forged")
            assert refused.value.sqlstate == "PX015"


def test_only_the_agent_runtime_builds_adk_agents() -> None:
    kernel = Path(pxos_kernel.__file__).parent
    builds = re.compile(r"^\s*(import|from)\s+google\.adk\.(agents|runners)\b", re.M)
    builders = {
        path.relative_to(kernel).as_posix()
        for path in kernel.rglob("*.py")
        if builds.search(path.read_text())
    }
    assert builders == {"agents/runtime.py"}
