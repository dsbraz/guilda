"""Exit criterion of slice 5 (PXOS-71): the scribe runs as a durable workflow.

A transcript goes in by reference; the scribe proposes the commitment, the card reaches
Ana, and the workflow waits for her. The worker shuts down during the wait and a new one
finishes the job once she decides. The Temporal history holds ids, never what was said.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.worker import Worker

from brq_at_work.scribe import SCRIBE, scribe_activities
from db_testing import Database
from pxos_kernel.channel import FakeChannel, decide, route_cards
from pxos_kernel.context import FakeContextStore
from pxos_kernel.identity import session
from pxos_kernel.ingestion import FakeConnector, SourceDocument
from pxos_kernel.model_access import FakeModels, ModelGateway
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Handler, create_pool, history, proposals_from, publish
from pxos_kernel.workflows import (
    AwaitReview,
    IngestSource,
    ingest_workflow_id,
    review_signals,
    workflow_runner,
)
from temporal_testing import history_bytes

SAID = "eu entrego o prot"  # ASCII, so it would appear as-is in any JSON payload
QUOTE = "Bruno: fechado, eu entrego o protótipo do onboarding até sexta, dia 28."
TRANSCRIPT = f"""\
Ana: bom dia, vamos repassar o status do app de onboarding.
{QUOTE}
Ana: ótimo, obrigada.
"""
COMMITMENT = (
    "COMPROMISSO | dono=bruno | prazo=2026-11-28 | frente=onboarding-app"
    f' | descrição=Entregar o protótipo do onboarding | trecho="{QUOTE}"'
)
# Nobody said this; the model made it up.
INVENTED = (
    "COMPROMISSO | dono=celia | prazo=2026-11-20 | frente=onboarding-app"
    ' | descrição=Publicar a carga de teste | trecho="Célia: eu publico a carga de teste dia 20."'
)


def transcript() -> SourceDocument:
    return SourceDocument(
        source=f"fixture:transcript/2026-11-14-status/{uuid4()}",
        version="1",
        text=TRANSCRIPT,
        occurred_at=datetime(2026, 11, 14, 14, tzinfo=UTC),
    )


@pytest.fixture
async def temporal(temporal_address: str) -> Client:
    return await Client.connect(temporal_address)


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    await publish(pool, {})  # events left by earlier tests are not ours
    yield pool
    await pool.close()


@asynccontextmanager
async def scribe(
    temporal: Client,
    pool: asyncpg.Pool,
    registry: Registry,
    document: SourceDocument,
    queue: str,
    models: FakeModels,
) -> AsyncIterator[None]:
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[IngestSource, AwaitReview],
        activities=scribe_activities(
            pool,
            registry,
            FakeContextStore({document.key: [INVENTED, COMMITMENT]}),
            FakeConnector([document]),
            ModelGateway(pool, models),
        ),
        workflow_runner=workflow_runner(),
    ):
        yield


async def proposal_from(pool: asyncpg.Pool, document: SourceDocument) -> UUID:
    for _ in range(300):
        async with session(pool, SCRIBE) as connection:
            found = await proposals_from(connection, document.key)
        if found:
            [proposal] = found
            return proposal.id
        await asyncio.sleep(0.1)
    raise AssertionError(f"no proposal from {document.key}")


async def start(
    temporal: Client, queue: str, document: SourceDocument
) -> WorkflowHandle[IngestSource, dict[str, str]]:
    return await temporal.start_workflow(
        IngestSource.run, document.key, id=ingest_workflow_id(document.key), task_queue=queue
    )


def handlers(
    temporal: Client, pool: asyncpg.Pool, registry: Registry, channel: FakeChannel
) -> dict[str, Handler]:
    return {"proposal.created": route_cards(pool, registry, channel), **review_signals(temporal)}


async def test_the_scribe_survives_a_worker_shutdown_while_ana_decides(
    temporal: Client,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    scribe_models: FakeModels,
) -> None:
    document = transcript()
    queue = f"scribe-{uuid4()}"
    channel = FakeChannel()
    outbox = handlers(temporal, pool, registry, channel)

    # 1-2: the transcript goes in by reference; only the commitment with a literal quote is
    # proposed, and its card reaches Ana.
    async with scribe(temporal, pool, registry, document, queue, scribe_models):
        handle = await start(temporal, queue, document)
        proposal_id = await proposal_from(pool, document)
        await publish(publisher, outbox)
        assert set(channel.cards) == {"ana"}
        [card] = channel.cards["ana"].values()
        assert card.proposal_id == proposal_id
    # 3: the worker has shut down; the workflow is still open, waiting.
    assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(handle.result()), timeout=1)

    # 4: Ana approves while no worker runs; the signal waits in Temporal.
    await decide(pool, registry, "ana", card, "approved")
    await publish(publisher, outbox)

    # 5: a new worker picks the workflow up and finishes it.
    async with scribe(temporal, pool, registry, document, queue, scribe_models):
        result = await asyncio.wait_for(handle.result(), timeout=30)
    assert result == {str(proposal_id): "approved"}
    async with session(pool, "celia") as connection:
        entity = await connection.fetchval(
            "SELECT entity_id FROM pxos.proposals WHERE id = $1", proposal_id
        )
        [fact] = await history(connection, entity)
    assert fact.evidence["quote"] == QUOTE

    # 6: nothing that was said is in the history.
    raw = await history_bytes(temporal, handle.id)
    assert str(proposal_id).encode() in raw
    assert SAID.encode() not in raw


async def test_a_rejection_on_the_card_also_ends_the_workflow(
    temporal: Client,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    scribe_models: FakeModels,
) -> None:
    document = transcript()
    queue = f"scribe-{uuid4()}"
    channel = FakeChannel()
    outbox = handlers(temporal, pool, registry, channel)

    async with scribe(temporal, pool, registry, document, queue, scribe_models):
        handle = await start(temporal, queue, document)
        proposal_id = await proposal_from(pool, document)
        await publish(publisher, outbox)
        [card] = channel.cards["ana"].values()
        await decide(pool, registry, "ana", card, "rejected", reason="já entregue")
        await publish(publisher, outbox)
        result = await asyncio.wait_for(handle.result(), timeout=30)

    assert result == {str(proposal_id): "rejected"}
