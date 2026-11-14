"""The configured route exists on Vertex AI (PXOS-75). Opt-in: real calls, billed to the
project.

Run with PXOS_VERTEX_PROJECT, application default credentials for that project and the
route variables of .env.example: the same route a deploy would configure. The prompts are
synthetic and minimal. The route is exercised the way the kernel uses it: the agent model
through the gateway, and the context memory's model and embeddings in the regions
Hindsight is configured with.
"""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest
from conftest import installation
from google import genai
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from db_testing import Database
from pxos_kernel.agents import AgentVersion, FactReader
from pxos_kernel.model_access import ModelGateway, Route, vertex_models
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

PROJECT = os.environ.get("PXOS_VERTEX_PROJECT", "")
pytestmark = pytest.mark.skipif(not PROJECT, reason="PXOS_VERTEX_PROJECT not set")


@pytest.fixture(scope="module")
def route() -> Route:
    return Route.from_environment()


@pytest.fixture(scope="module")
def deployed(registry: Registry, route: Route) -> Database:
    return installation("pxos_test_vertex", registry, route)


@pytest.fixture
async def gateway(deployed: Database) -> AsyncIterator[ModelGateway]:
    pool: asyncpg.Pool = await create_pool(deployed.app_dsn)
    yield ModelGateway(pool, vertex_models(PROJECT))
    await pool.close()


async def test_an_agent_answers_through_the_gateway(gateway: ModelGateway) -> None:
    agent = LlmAgent(name="probe", model=await gateway.adk_model(), instruction="Responda: ok")
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="pxos-probe", user_id="probe")
    runner = Runner(agent=agent, app_name="pxos-probe", session_service=sessions)
    answer = ""
    try:
        async for event in runner.run_async(
            user_id="probe",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="teste")]),
        ):
            if event.is_final_response() and event.content:
                answer = "".join(part.text or "" for part in event.content.parts or [])
    finally:
        await runner.close()
    assert "ok" in answer.lower()


def test_the_context_memory_model_answers_in_its_region(route: Route) -> None:
    client = genai.Client(vertexai=True, project=PROJECT, location=route.memory_region)
    response = client.models.generate_content(
        model=route.memory_model, contents="Responda apenas: ok"
    )
    assert "ok" in (response.text or "").lower()


def test_embeddings_are_computed_in_the_route_region(route: Route) -> None:
    client = genai.Client(vertexai=True, project=PROJECT, location=route.embedding_region)
    response = client.models.embed_content(
        model=route.embedding_model,
        contents="texto sintético",
        config=types.EmbedContentConfig(output_dimensionality=768),
    )
    assert response.embeddings and len(response.embeddings[0].values or []) == 768


async def test_the_adk_step_reads_a_fact_on_vertex(
    registry: Registry, gateway: ModelGateway
) -> None:
    """The ADK step's derived schema works on the configured model (PXOS-79)."""
    version = AgentVersion(
        agent="restock-agent",
        entity_id=uuid4(),
        revision=1,
        digest="probe",
        instructions=(
            "You read one extracted fact. If it is a restock promise, answer with the payload "
            "(sku, quantity, occurred_at = 2026-11-14T09:00:00Z) and the verbatim quote it "
            "cites. Otherwise answer with payload and quote null."
        ),
        memory_instructions="unused",
        proposes=("retail", "create_restock_order"),
    )
    model = await gateway.adk_model()
    reader = FactReader(model, version, registry.get("retail").models["RestockOrder"])
    read = await reader.read(
        'Erin promised 12 boxes of A-1. Verbatim: "Erin: I will restock aisle 3 with 12 boxes"'
    )
    assert read is not None
    payload, quote = read
    assert (payload["sku"], payload["quantity"]) == ("A-1", 12)
    assert quote == "Erin: I will restock aisle 3 with 12 boxes"
    assert await reader.read("Carla thanked Erin for the help.") is None
