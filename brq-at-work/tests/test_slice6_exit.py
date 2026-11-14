"""Exit criterion of slice 6 (PXOS-76): BRQ@work declares its residency, and models obey.

BRQ@work installs with residency in Brazil unless told otherwise. Two installations side
by side get their own routes; the Brazilian one never reaches the global endpoint, not
through an ADK agent and not through Hindsight's configuration. The scribe's cycle of
slice 2 (test_slice2_exit) runs on this same Brazilian installation.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from brq_at_work.composition import bootstrap
from db_testing import Database, create_test_database
from pxos_kernel.context.hindsight import server_settings
from pxos_kernel.model_access import ROUTES, FakeModels, ModelGateway, Route
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool


@pytest.fixture(scope="module")
def global_installation(registry: Registry) -> Database:
    database = create_test_database("pxos_test_brq_global")
    bootstrap(database.owner_dsn, registry, residency="global")
    return database


@pytest.fixture
async def global_pool(global_installation: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(global_installation.app_dsn)
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


def regions(route: Route) -> set[str]:
    settings = server_settings(route, project="p")
    return {route.region, *settings.values()}


async def test_brq_at_work_keeps_a_br_installation_in_brazil(
    pool: asyncpg.Pool, global_pool: asyncpg.Pool
) -> None:
    # 1: installed with residency in Brazil by default.
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT residency FROM pxos.installation") == "br"

    # 2: each installation gets its own route.
    models = FakeModels(reply="em dia")
    br, world = ModelGateway(pool, models), ModelGateway(global_pool, models)
    assert await br.route() == ROUTES["br"]
    assert await world.route() == ROUTES["global"]

    # 3: an ADK agent on the br installation answers through the gateway, on the br route.
    assert await answer(br) == "em dia"
    assert models.routes == [ROUTES["br"]]

    # 4: neither the agent's route nor Hindsight's configuration leaves Brazil.
    assert not regions(await br.route()) & {"global", "us-central1"}
    assert "global" in regions(await world.route())
