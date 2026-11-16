"""Exit criterion of slice 7 (PXOS-80): the scribe runs from its published version.

On a fresh installation the scribe does not run: nothing seeds a definition around the
review. Bruno proposes the scribe's definition, the card reaches Eva, who holds
platform-admin, and she approves it. From then on the transcript, by reference, becomes a
commitment through the ADK step, and the run points to the version Eva approved. A new
approved version is what the next run uses.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ActivityError
from temporalio.worker import Worker

from brq_at_work.composition import bootstrap
from brq_at_work.scribe import DRAFT, SCRIBE, scribe_activities
from db_testing import Database, create_test_database
from model_testing import ROUTE
from pxos_kernel.agents import AGENTS, approved_version, recorded_run
from pxos_kernel.channel import FakeChannel, decide, route_cards
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import FakeConnector, SourceDocument
from pxos_kernel.model_access import FakeModels, ModelGateway
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Evidence, Proposal, create_pool, propose, publish
from pxos_kernel.workflows import (
    AwaitReview,
    IngestSource,
    ingest_workflow_id,
    review_signals,
    workflow_runner,
)

QUOTE = "Bruno: fechado, eu entrego o protótipo do onboarding até sexta, dia 28."
TRANSCRIPT = f"Ana: vamos repassar o status.\n{QUOTE}\nAna: ótimo, obrigada.\n"
COMMITMENT = (
    "COMPROMISSO | dono=bruno | prazo=2026-11-28 | frente=onboarding-app"
    f' | descrição=Entregar o protótipo do onboarding | trecho="{QUOTE}"'
)


@pytest.fixture(scope="module")
def fresh(registry: Registry) -> Database:
    database = create_test_database("pxos_test_brq_slice7")
    bootstrap(database.owner_dsn, registry, route=ROUTE)
    return database


@pytest.fixture
async def fresh_pool(fresh: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(fresh.app_dsn)
    yield pool
    await pool.close()


@pytest.fixture
async def publisher(fresh: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(fresh.publisher_dsn)
    yield pool
    await pool.close()


async def bruno_proposes(
    pool: asyncpg.Pool,
    registry: Registry,
    payload: dict[str, Any],
    quote: str,
    *,
    entity_id: UUID | None = None,
    base_revision: int = 0,
) -> Proposal:
    async with session(pool, "bruno") as connection:
        return await propose(
            connection,
            registry.get(AGENTS),
            "publish_agent_version",
            payload=payload,
            evidence=Evidence(source="test:bruno", quote=quote, context_ref=f"test:{uuid4()}"),
            valid_from=datetime.now(UTC),
            entity_id=entity_id,
            base_revision=base_revision,
        )


def transcript() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:transcript/2026-11-14-status/{uuid4()}",
        version="1",
        text=TRANSCRIPT,
        occurred_at=datetime(2026, 11, 14, 14, tzinfo=UTC),
    )


async def ingest_by_reference(
    temporal: Client,
    pool: asyncpg.Pool,
    registry: Registry,
    models: FakeModels,
    document: SourceDocument,
    outbox: dict[str, Any],
    publisher: asyncpg.Pool,
    then: Callable[[], Any] | None = None,
) -> dict[str, str]:
    queue = f"scribe-{uuid4()}"
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[IngestSource, AwaitReview],
        activities=scribe_activities(
            pool,
            registry,
            FakeContextStore({document.key: [COMMITMENT]}),
            FakeConnector([document]),
            ModelGateway(pool, models),
        ),
        workflow_runner=workflow_runner(),
    ):
        handle = await temporal.start_workflow(
            IngestSource.run, document.key, id=ingest_workflow_id(document.key), task_queue=queue
        )
        if then is None:
            return await asyncio.wait_for(handle.result(), timeout=30)
        for _ in range(300):
            await publish(publisher, outbox)
            if await then():
                await publish(publisher, outbox)  # the decision's signal to the workflow
                break
            await asyncio.sleep(0.1)
        return await asyncio.wait_for(handle.result(), timeout=30)


async def test_the_scribe_runs_only_from_the_version_eva_approved(
    temporal_address: str,
    fresh_pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    scribe_models: FakeModels,
) -> None:
    temporal = await Client.connect(temporal_address)
    channel = FakeChannel()
    outbox = {"proposal.created": route_cards(fresh_pool, registry, channel)}
    outbox.update(review_signals(temporal))

    # 1: a fresh installation has no approved version; the scribe does not run.
    with pytest.raises(WorkflowFailureError) as failed:
        await ingest_by_reference(
            temporal, fresh_pool, registry, scribe_models, transcript(), outbox, publisher
        )
    assert isinstance(failed.value.cause, ActivityError)
    assert "NoApprovedVersion" in str(failed.value.cause.cause)
    assert scribe_models.requests == []

    # 2: Bruno proposes the definition; the card goes to Eva, not to Ana.
    proposed = await bruno_proposes(fresh_pool, registry, DRAFT, "v1")
    await publish(publisher, outbox)
    assert set(channel.cards) == {"eva"}
    [card] = channel.cards["eva"].values()
    assert card.title == "Versão de agente"
    await decide(fresh_pool, registry, "eva", card, "approved")

    # 3: the transcript, by reference, becomes a commitment on Ana's card via the ADK step.
    decided: set[UUID] = set()

    async def ana_approves() -> bool:
        """Ana approves the cards she has not seen yet; true once she approved one."""
        new = [c for c in channel.cards.get("ana", {}).values() if c.proposal_id not in decided]
        for card in new:
            await decide(fresh_pool, registry, "ana", card, "approved")
            decided.add(card.proposal_id)
        return bool(new)

    first = transcript()
    result = await ingest_by_reference(
        temporal, fresh_pool, registry, scribe_models, first, outbox, publisher, ana_approves
    )
    assert list(result.values()) == ["approved"]
    async with session(fresh_pool, SCRIBE) as connection:
        version = await approved_version(connection, SCRIBE)
        run = await recorded_run(connection, first.key)
    assert run is not None and (run.definition, run.revision) == (proposed.entity_id, 1)
    assert any(
        DRAFT["instructions"] in str(r.config.system_instruction) for r in scribe_models.requests
    )

    # 4: a new approved version is what the next run uses.
    changed = await bruno_proposes(
        fresh_pool,
        registry,
        {**DRAFT, "instructions": f"{DRAFT['instructions']} Seja breve."},
        "v2",
        entity_id=version.entity_id,
        base_revision=version.revision,
    )
    await publish(publisher, outbox)
    [card] = [c for c in channel.cards["eva"].values() if c.proposal_id == changed.id]
    await decide(fresh_pool, registry, "eva", card, "approved")
    second = transcript()
    await ingest_by_reference(
        temporal, fresh_pool, registry, scribe_models, second, outbox, publisher, ana_approves
    )
    async with session(fresh_pool, SCRIBE) as connection:
        run = await recorded_run(connection, second.key)
    assert run is not None and (run.definition, run.revision) == (version.entity_id, 2)
