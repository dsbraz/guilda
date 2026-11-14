"""An ADK agent reaches a model only through the gateway (PXOS-73, ADR 0004 §2, rule 1).

The gateway hands out an ADK model bound to the installation's route. The agent runs on
the real ADK runner; only the provider behind the route is a fake, which records the
route each call went to.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from db_testing import Database
from model_testing import OTHER_ROUTE, ROUTE
from pxos_kernel.model_access import FakeModels, ModelGateway, ModelNotAllowed
from pxos_kernel.state import create_pool


@pytest.fixture
async def other_pool(other_database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(other_database.app_dsn)
    yield pool
    await pool.close()


async def ask(agent: LlmAgent, text: str) -> str:
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name="pxos-test", user_id="erin")
    runner = Runner(agent=agent, app_name="pxos-test", session_service=sessions)
    answer = ""
    try:
        async for event in runner.run_async(
            user_id="erin",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)]),
        ):
            if event.is_final_response() and event.content:
                answer = "".join(part.text or "" for part in event.content.parts or [])
    finally:
        await runner.close()
    return answer


async def test_an_agent_calls_only_the_installation_route(
    pool: asyncpg.Pool,
) -> None:
    models = FakeModels(reply="ok")
    agent = LlmAgent(name="reader", model=await ModelGateway(pool, models).adk_model())

    assert await ask(agent, "olá") == "ok"
    assert models.routes == [ROUTE]


async def test_each_installation_answers_from_its_own_route(
    pool: asyncpg.Pool, other_pool: asyncpg.Pool
) -> None:
    models = FakeModels(reply="ok")
    for each in (pool, other_pool):
        await ask(LlmAgent(name="reader", model=await ModelGateway(each, models).adk_model()), "x")
    assert models.routes == [ROUTE, OTHER_ROUTE]


async def test_a_request_for_another_model_is_refused(pool: asyncpg.Pool) -> None:
    models = FakeModels(reply="ok")

    def elsewhere(callback_context: CallbackContext, llm_request: LlmRequest) -> None:
        llm_request.model = OTHER_ROUTE.model

    agent = LlmAgent(
        name="reader",
        model=await ModelGateway(pool, models).adk_model(),
        before_model_callback=elsewhere,
    )
    with pytest.raises(ModelNotAllowed):
        await ask(agent, "olá")
    assert models.routes == []
