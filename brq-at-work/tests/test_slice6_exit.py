"""Exit criterion of slice 6 (K76): each BRQ@work installation uses the route its deploy
configured, and models obey.

Which provider, model and regions a client's installation uses is decided at deploy, from
the environment (.env.example); BRQ@work fixes none of them. Two installations side by side
get their own routes, and an ADK agent and Hindsight's configuration stay on them.
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from brq_at_work.composition import bootstrap
from db_testing import Database, create_test_database
from fakes_testing import FakeModels
from guilda_kernel.context.hindsight import server_settings
from guilda_kernel.model_access import ModelGateway
from guilda_kernel.ontology import Registry
from guilda_kernel.state import create_pool
from model_testing import OTHER_ROUTE, ROUTE

SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def other_installation(registry: Registry) -> Database:
    database = create_test_database("guilda_test_brq_other_route")
    bootstrap(database.owner_dsn, registry, route=OTHER_ROUTE)
    return database


@pytest.fixture
async def other_pool(other_installation: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(other_installation.app_dsn)
    yield pool
    await pool.close()


async def answer(gateway: ModelGateway) -> str:
    agent = LlmAgent(name="probe", model=await gateway.adk_model())
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="brq-probe", user_id="ana")
    runner = Runner(agent=agent, app_name="brq-probe", session_service=sessions)
    text = ""
    try:
        async for event in runner.run_async(
            user_id="ana",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="status?")]),
        ):
            if event.is_final_response() and event.content:
                text = "".join(part.text or "" for part in event.content.parts or [])
    finally:
        await runner.close()
    return text


def test_brq_at_work_fixes_no_model_or_region() -> None:
    fixed = re.compile(
        r"(gemini|claude)-[0-9a-z.-]+|(southamerica|northamerica|us|europe|asia|me|africa)"
        r"-[a-z]+[0-9]|['\"]global['\"]|residen"
    )
    found = [
        f"{path.relative_to(SOURCE)}: {match.group()}"
        for path in SOURCE.rglob("*.py")
        for match in fixed.finditer(path.read_text(encoding="utf-8"))
    ]
    assert found == []


async def test_each_installation_stays_on_the_route_of_its_deploy(
    pool: asyncpg.Pool, other_pool: asyncpg.Pool
) -> None:
    # 1: each installation gets the route it was deployed with.
    models = FakeModels(reply="em dia")
    first, other = ModelGateway(pool, models), ModelGateway(other_pool, models)
    assert await first.route() == ROUTE
    assert await other.route() == OTHER_ROUTE

    # 2: an ADK agent answers through the gateway, on its installation's route.
    assert await answer(first) == "em dia"
    assert models.routes == [ROUTE]

    # 3: Hindsight's configuration follows the same route.
    settings = server_settings(await first.route(), project="p")
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_REGION"] == ROUTE.memory_region
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION"] == ROUTE.embedding_region
    assert not set(settings.values()) & {
        OTHER_ROUTE.region,
        OTHER_ROUTE.memory_region,
        OTHER_ROUTE.embedding_region,
    }
