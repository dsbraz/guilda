"""The ADK step reads each extracted fact from the version in force (PXOS-79, K34, K36).

Ingestion runs as the agent, from its approved version: the context memory extracts with
the version's memory instructions, and an ADK agent with the version's instructions reads
each fact into a candidate of the action the version proposes. The model comes from the
gateway. The kernel still checks the quote against the source, and records which version
ran in the same transaction as the proposals.
"""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, MEMORY_INSTRUCTIONS, RESTOCK_AGENT, installation
from google.adk.models.llm_request import LlmRequest

import pxos_kernel
from agents_testing import publish, scripted
from db_testing import Database
from model_testing import ROUTE
from pxos_kernel.agents import (
    AgentVersion,
    FactReader,
    NoApprovedVersion,
    approved_version,
    recorded_run,
)
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import SourceDocument, ingest
from pxos_kernel.model_access import FakeModels, GatewayModel, ModelGateway, ModelNotAllowed
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

LOG = "Erin: I will restock aisle 3 with 12 boxes of A-1 tomorrow.\nCarla: thanks.\n"
QUOTE = "Erin: I will restock aisle 3 with 12 boxes of A-1"
FACT = "Erin promised to restock A-1"
ORDER = {"sku": "A-1", "quantity": 12, "occurred_at": "2026-11-14T09:00:00Z"}


def read(fact: str) -> dict[str, Any] | None:
    return {"payload": ORDER, "quote": QUOTE} if fact == FACT else None


def document() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:shelf-log/{uuid4()}",
        version="1",
        text=LOG,
        occurred_at=datetime(2026, 11, 14, 9, tzinfo=UTC),
    )


async def ingested(
    pool: asyncpg.Pool,
    registry: Registry,
    models: FakeModels,
    doc: SourceDocument,
    store: FakeContextStore | None = None,
) -> list[str]:
    store = store or FakeContextStore({doc.key: [FACT, "Carla thanked Erin"]})
    model = await ModelGateway(pool, models).adk_model()
    proposals = await ingest(pool, registry, store, doc, model, agent=AGENT)
    return [proposal.evidence["quote"] for proposal in proposals]


@pytest.fixture
def unpublished(registry: Registry) -> Iterator[Database]:
    yield installation("pxos_test_unpublished", registry, ROUTE)


@pytest.fixture
async def unpublished_pool(unpublished: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(unpublished.app_dsn)
    yield pool
    await pool.close()


async def test_without_an_approved_version_nothing_runs(
    unpublished: Database, unpublished_pool: asyncpg.Pool, registry: Registry
) -> None:
    models = FakeModels(reply=scripted(read))
    doc = document()
    with pytest.raises(NoApprovedVersion):
        await ingested(unpublished_pool, registry, models, doc)
    assert models.requests == []
    owner = await asyncpg.connect(unpublished.owner_dsn)
    try:
        assert await owner.fetchval("SELECT count(*) FROM pxos.sources") == 0
    finally:
        await owner.close()


async def test_the_agent_reads_each_fact_with_the_versions_instructions(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    models = FakeModels(reply=scripted(read))
    doc = document()
    store = FakeContextStore({doc.key: [FACT, "Carla thanked Erin"]})

    assert await ingested(pool, registry, models, doc, store) == [QUOTE]
    assert store.retained == [(doc.key, MEMORY_INSTRUCTIONS)]
    assert len(models.requests) == 2  # one ADK run per extracted fact
    for request in models.requests:
        assert RESTOCK_AGENT["instructions"] in str(request.config.system_instruction)
    assert set(models.routes) == {ROUTE}


async def test_a_quote_the_model_made_up_is_not_proposed(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    def invents(fact: str) -> dict[str, Any] | None:
        return {"payload": ORDER, "quote": "Erin: I will restock everything"}

    assert await ingested(pool, registry, FakeModels(reply=scripted(invents)), document()) == []


async def test_an_answer_outside_the_actions_schema_is_dropped(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    def wrong(fact: str) -> dict[str, Any] | None:
        return {"payload": {**ORDER, "quantity": 0}, "quote": QUOTE}

    assert await ingested(pool, registry, FakeModels(reply=scripted(wrong)), document()) == []


async def test_each_run_records_the_version_it_ran(pool: asyncpg.Pool, registry: Registry) -> None:
    models = FakeModels(reply=scripted(read))
    async with session(pool, AGENT) as connection:
        before = await approved_version(connection, AGENT)
    first = document()
    await ingested(pool, registry, models, first)

    changed = {**RESTOCK_AGENT, "instructions": f"{RESTOCK_AGENT['instructions']} Be brief."}
    await publish(pool, registry, changed, author="gina", approver="paula", change=before)
    second = document()
    await ingested(pool, registry, models, second)

    async with session(pool, AGENT) as connection:
        ran = [await recorded_run(connection, doc.key) for doc in (first, second)]
    assert [(r.definition, r.revision) for r in ran if r] == [
        (before.entity_id, before.revision),
        (before.entity_id, before.revision + 1),
    ]


async def test_a_model_failure_leaves_no_trace_and_can_be_retried(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    def broken(request: LlmRequest) -> str:
        raise RuntimeError("model unavailable")

    doc = document()
    with pytest.raises(RuntimeError):
        await ingested(pool, registry, FakeModels(reply=broken), doc)
    async with session(pool, AGENT) as connection:
        assert await recorded_run(connection, doc.key) is None
    assert await ingested(pool, registry, FakeModels(reply=scripted(read)), doc) == [QUOTE]


async def test_the_application_cannot_forge_or_change_a_run(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    doc = document()
    await ingested(pool, registry, FakeModels(reply=scripted(read)), doc)
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


async def test_the_adk_step_runs_only_on_the_gateways_model(registry: Registry) -> None:
    """A model built around the gateway is refused, whatever the agent's id looks like."""
    models = FakeModels(reply=scripted(read))
    version = AgentVersion(
        agent="shelf.agent-2",  # actor ids may hold dots and hyphens
        entity_id=uuid4(),
        revision=1,
        digest="d",
        instructions="Read it.",
        memory_instructions="Extract it.",
        proposes=("retail", "create_restock_order"),
    )
    payload = registry.get("retail").models["RestockOrder"]
    with pytest.raises(ModelNotAllowed):
        FactReader(models(ROUTE), version, payload)  # type: ignore[arg-type]
    reader = FactReader(GatewayModel(ROUTE, models(ROUTE)), version, payload)
    assert await reader.read(FACT) == ({**ORDER, "occurred_at": "2026-11-14T09:00:00Z"}, QUOTE)


def test_only_the_agent_runtime_builds_adk_agents() -> None:
    """Every agent the kernel builds gets its model from the gateway (ADR 0004 §2, rule 1).
    A distribution builds none; its own suite checks it imports nothing from ADK."""
    kernel = Path(pxos_kernel.__file__).parent
    builds = re.compile(r"^\s*(import|from)\s+google\.adk\.(agents|runners)\b", re.M)
    builders = {
        path.relative_to(kernel).as_posix()
        for path in kernel.rglob("*.py")
        if builds.search(path.read_text())
    }
    assert builders == {"agents/runtime.py"}
