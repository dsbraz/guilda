"""The routes exist on Vertex AI (PXOS-75). Opt-in: real calls, billed to the project.

Run with PXOS_VERTEX_PROJECT and application default credentials for that project. The
prompts are synthetic and minimal. Each route is exercised the way the kernel uses it:
the agent model through the gateway, and the context memory's model and embeddings in
the regions Hindsight is configured with.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid4

import asyncpg
import pytest
from google import genai
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from db_testing import Database
from pxos_kernel.agents import AgentVersion, FactReader
from pxos_kernel.model_access import (
    ROUTES,
    GatewayModel,
    ModelGateway,
    Residency,
    vertex_models,
)
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

PROJECT = os.environ.get("PXOS_VERTEX_PROJECT", "")
pytestmark = pytest.mark.skipif(not PROJECT, reason="PXOS_VERTEX_PROJECT not set")


@pytest.fixture
async def pools(
    database: Database, other_database: Database
) -> AsyncIterator[dict[Residency, asyncpg.Pool]]:
    br = await create_pool(database.app_dsn)
    world = await create_pool(other_database.app_dsn)
    yield {"br": br, "global": world}
    await br.close()
    await world.close()


@pytest.mark.parametrize("residency", ["br", "global"])
async def test_an_agent_answers_through_the_gateway(
    pools: dict[Residency, asyncpg.Pool], residency: Residency
) -> None:
    model = await ModelGateway(pools[residency], vertex_models(PROJECT)).adk_model()
    agent = LlmAgent(name="probe", model=model, instruction="Responda apenas: ok")
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


@pytest.mark.parametrize("residency", ["br", "global"])
def test_the_context_memory_model_answers_in_its_region(residency: Residency) -> None:
    route = ROUTES[residency]
    client = genai.Client(vertexai=True, project=PROJECT, location=route.memory_region)
    response = client.models.generate_content(
        model=route.memory_model, contents="Responda apenas: ok"
    )
    assert "ok" in (response.text or "").lower()


@pytest.mark.parametrize("residency", ["br", "global"])
def test_embeddings_are_computed_in_the_route_region(residency: Residency) -> None:
    route = ROUTES[residency]
    client = genai.Client(vertexai=True, project=PROJECT, location=route.embedding_region)
    response = client.models.embed_content(
        model=route.embedding_model,
        contents="texto sintético",
        config=types.EmbedContentConfig(output_dimensionality=768),
    )
    assert response.embeddings and len(response.embeddings[0].values or []) == 768


async def test_the_adk_step_reads_a_fact_on_vertex(registry: Registry) -> None:
    """The ADK step's derived schema works on Vertex (PXOS-79). The br route has no
    generation model in São Paulo yet (PXOS-75), so this runs the same Gemini model on the
    global endpoint; it checks the schema and the parsing, not residency."""
    route = replace(ROUTES["br"], region="global")
    model = GatewayModel(route, vertex_models(PROJECT)(route))
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
    reader = FactReader(model, version, registry.get("retail").models["RestockOrder"])
    read = await reader.read(
        'Erin promised 12 boxes of A-1. Verbatim: "Erin: I will restock aisle 3 with 12 boxes"'
    )
    assert read is not None
    payload, quote = read
    assert (payload["sku"], payload["quantity"]) == ("A-1", 12)
    assert quote == "Erin: I will restock aisle 3 with 12 boxes"
    assert await reader.read("Carla thanked Erin for the help.") is None
