"""Model access follows the route the installation was deployed with (PXOS-72, PRD §6.4,
ADR 0004 §2).

Which provider, model and regions an installation uses is a deploy decision: the FDE sets
it per client in the environment, the bootstrap records it, and the gateway builds model
clients for that route only. Nothing about models or regions is fixed in code.
"""

import re
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from anthropic import AsyncAnthropicVertex
from google.adk.models.anthropic_llm import Claude
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from db_testing import Database, create_test_database
from model_testing import OTHER_ROUTE, ROUTE
from pxos_kernel.model_access import (
    FakeModels,
    GatewayModel,
    ModelGateway,
    ModelNotAllowed,
    NoRoute,
    Route,
    RouteNotConfigured,
    install_route,
    issued_by_gateway,
    vertex_models,
)
from pxos_kernel.state import create_pool

SOURCE = Path(__file__).resolve().parents[1] / "src"

ENVIRONMENT = {
    "PXOS_MODEL_PROVIDER": "claude",
    "PXOS_MODEL": "model-c",
    "PXOS_MODEL_REGION": "region-c",
    "PXOS_MEMORY_MODEL": "memory-model-c",
    "PXOS_MEMORY_REGION": "memory-region-c",
    "PXOS_EMBEDDING_MODEL": "embedding-model-c",
    "PXOS_EMBEDDING_REGION": "embedding-region-c",
}


@pytest.fixture
async def other_pool(other_database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(other_database.app_dsn)
    yield pool
    await pool.close()


def endpoint_host(model: object) -> str:
    """The host the model's client would call, whatever the provider."""
    client = model.client  # type: ignore[attr-defined]
    base_url = getattr(client, "base_url", None)
    if base_url is None:  # google-genai
        base_url = client._api_client._http_options.base_url
    return urlparse(str(base_url)).hostname or ""


def test_the_route_comes_from_the_environment() -> None:
    assert Route.from_environment(ENVIRONMENT) == Route(
        provider="claude",
        model="model-c",
        region="region-c",
        memory_model="memory-model-c",
        memory_region="memory-region-c",
        embedding_model="embedding-model-c",
        embedding_region="embedding-region-c",
    )


@pytest.mark.parametrize("missing", sorted(ENVIRONMENT))
def test_a_route_missing_a_setting_is_refused(missing: str) -> None:
    for environment in (
        {k: v for k, v in ENVIRONMENT.items() if k != missing},
        {**ENVIRONMENT, missing: " "},
    ):
        with pytest.raises(RouteNotConfigured, match=missing):
            Route.from_environment(environment)


def test_a_provider_the_kernel_has_no_client_for_is_refused() -> None:
    with pytest.raises(
        RouteNotConfigured, match="PXOS_MODEL_PROVIDER must be one of gemini, claude"
    ):
        Route.from_environment({**ENVIRONMENT, "PXOS_MODEL_PROVIDER": "other"})


def test_no_model_or_region_is_fixed_in_the_kernel() -> None:
    fixed = re.compile(
        r"(gemini|claude)-[0-9a-z.-]+|(southamerica|northamerica|us|europe|asia|me|africa)"
        r"-[a-z]+[0-9]|['\"]global['\"]"
    )
    found = [
        f"{path.relative_to(SOURCE)}: {match.group()}"
        for path in SOURCE.rglob("*.py")
        if "migrations" not in path.parts  # past migrations are history, not configuration
        for match in fixed.finditer(path.read_text(encoding="utf-8"))
    ]
    assert found == []


async def test_each_installation_gets_the_route_it_was_deployed_with(
    pool: asyncpg.Pool, other_pool: asyncpg.Pool
) -> None:
    assert await ModelGateway(pool, vertex_models("p")).route() == ROUTE
    assert await ModelGateway(other_pool, vertex_models("p")).route() == OTHER_ROUTE


@pytest.mark.parametrize("route", [ROUTE, OTHER_ROUTE], ids=["gemini", "claude"])
def test_the_client_calls_only_the_route_region(route: Route) -> None:
    model = vertex_models("p")(route)
    assert model.model == route.model
    assert endpoint_host(model) == f"{route.region}-aiplatform.googleapis.com"


def test_each_provider_gets_its_own_client_in_the_deploy_project() -> None:
    gemini = vertex_models("project-p")(replace(ROUTE, provider="gemini"))
    claude = vertex_models("project-p")(replace(ROUTE, provider="claude"))
    assert isinstance(gemini, Gemini)
    assert gemini.api_client._api_client.project == "project-p"
    assert isinstance(claude, Claude)
    assert isinstance(claude.client, AsyncAnthropicVertex)
    assert claude.client.project_id == "project-p"


class Recorder(BaseLlm):
    calls: list[tuple[LlmRequest, bool]] = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls.append((llm_request, stream))
        yield LlmResponse()


async def answers(model: BaseLlm, request: LlmRequest, **stream: bool) -> list[LlmResponse]:
    return [response async for response in model.generate_content_async(request, **stream)]


async def test_the_gateway_model_passes_the_request_to_the_route_provider() -> None:
    provider = Recorder(model=ROUTE.model, calls=[])
    model = GatewayModel(ROUTE, provider)
    request = LlmRequest(model=ROUTE.model)

    assert len(await answers(model, request)) == 1
    assert len(await answers(model, request, stream=True)) == 1
    assert provider.calls == [(request, False), (request, True)]


async def test_the_gateway_model_refuses_another_model() -> None:
    model = GatewayModel(ROUTE, Recorder(model=ROUTE.model, calls=[]))
    with pytest.raises(ModelNotAllowed, match="other-model is not this installation's model"):
        await answers(model, LlmRequest(model="other-model"))


async def test_fake_models_answer_from_the_request_and_record_it() -> None:
    models = FakeModels(reply=lambda request: f"answer to {request.model}")
    request = LlmRequest(model=ROUTE.model)

    (response,) = await answers(models(ROUTE), request)

    assert response.content is not None
    assert response.content.role == "model"
    assert [part.text for part in response.content.parts or []] == [f"answer to {ROUTE.model}"]
    assert models.requests == [request]
    assert models.routes == [ROUTE]


async def test_only_a_model_the_gateway_issued_counts_as_issued(pool: asyncpg.Pool) -> None:
    models = FakeModels(reply="ok")
    issued = await ModelGateway(pool, models).adk_model()
    assert issued_by_gateway(issued)
    assert not issued_by_gateway(GatewayModel(ROUTE, models(ROUTE)))


@pytest.fixture(scope="module")
def unconfigured() -> Database:
    return create_test_database("pxos_test_no_route")


async def test_an_installation_without_a_route_gets_no_model(unconfigured: Database) -> None:
    pool = await create_pool(unconfigured.app_dsn)
    try:
        with pytest.raises(NoRoute, match="the installation has no route; it gets no model"):
            await ModelGateway(pool, vertex_models("p")).route()
    finally:
        await pool.close()


async def test_the_application_reads_the_route_but_cannot_change_it(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT region FROM pxos.installation") == ROUTE.region
        for statement in (
            "UPDATE pxos.installation SET region = 'elsewhere'",
            "DELETE FROM pxos.installation",
            "INSERT INTO pxos.installation (provider, model, region, memory_model, memory_region,"
            " embedding_model, embedding_region) VALUES ('gemini', 'm', 'r', 'm', 'r', 'm', 'r')",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)


async def test_a_new_deploy_replaces_the_route(unconfigured: Database) -> None:
    owner = await asyncpg.connect(unconfigured.owner_dsn)
    try:
        await install_route(owner, ROUTE)
        await install_route(owner, OTHER_ROUTE)
        assert await owner.fetchval("SELECT count(*) FROM pxos.installation") == 1
        assert await owner.fetchval("SELECT model FROM pxos.installation") == OTHER_ROUTE.model
        with pytest.raises(asyncpg.CheckViolationError):
            await install_route(owner, replace(ROUTE, provider="other"))  # type: ignore[arg-type]
        with pytest.raises(asyncpg.CheckViolationError):
            await install_route(owner, replace(ROUTE, region=""))
    finally:
        await owner.execute("DELETE FROM pxos.installation")
        await owner.close()
