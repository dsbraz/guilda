"""Model access by the installation's route (PXOS-72, K11; PRD §6.4; ADR 0004 §2, rule 1).

Which provider, model and regions an installation uses is decided at deploy: the FDE sets
them per client in the environment (see .env.example), and the bootstrap records them as
the installation's single route. Every model call of that installation uses it, and the
gateway builds clients for that route only; nobody asks it for a model or a region. The
kernel fixes no model and no region.
"""

import os
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import astuple, dataclass, fields
from typing import Any, Literal, Self, get_args

import asyncpg
from anthropic import AsyncAnthropicVertex
from google import genai
from google.adk.models.anthropic_llm import Claude
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from pxos_kernel.errors import PxosError

Provider = Literal["gemini", "claude"]
"""The providers the kernel has a client for."""


class RouteNotConfigured(PxosError):
    pass


class NoRoute(PxosError):
    pass


class ModelNotAllowed(PxosError):
    pass


@dataclass(frozen=True)
class Route:
    provider: Provider
    model: str
    region: str
    memory_model: str
    """The model the context memory (Hindsight) extracts with; it calls Vertex itself."""
    memory_region: str
    embedding_model: str
    embedding_region: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ) -> Self:
        """The route the deploy configured; a missing setting or unknown provider is refused."""
        values: dict[str, Any] = {}
        for field in fields(cls):
            name = _VARIABLES[field.name]
            value = environment.get(name, "").strip()
            if not value:
                raise RouteNotConfigured(f"{name} is not set")
            values[field.name] = value
        if values["provider"] not in get_args(Provider):
            raise RouteNotConfigured(
                f"PXOS_MODEL_PROVIDER must be one of {', '.join(get_args(Provider))}"
            )
        return cls(**values)


_VARIABLES = {
    "provider": "PXOS_MODEL_PROVIDER",
    "model": "PXOS_MODEL",
    "region": "PXOS_MODEL_REGION",
    "memory_model": "PXOS_MEMORY_MODEL",
    "memory_region": "PXOS_MEMORY_REGION",
    "embedding_model": "PXOS_EMBEDDING_MODEL",
    "embedding_region": "PXOS_EMBEDDING_REGION",
}

_COLUMNS = ", ".join(field.name for field in fields(Route))

Providers = Callable[[Route], BaseLlm]
"""Builds the model client for a route."""


async def install_route(owner: asyncpg.Connection, route: Route) -> None:
    """Record the installation's route, as the owner. A new deploy replaces it."""
    updates = ", ".join(f"{f.name} = EXCLUDED.{f.name}" for f in fields(Route))
    await owner.execute(
        f"INSERT INTO pxos.installation ({_COLUMNS}) VALUES ($1, $2, $3, $4, $5, $6, $7)"
        f" ON CONFLICT (singleton) DO UPDATE SET {updates}",
        *astuple(route),
    )


class GatewayModel(BaseLlm):
    """The ADK model an agent gets from the gateway: every call goes to the route's
    provider, and a request for any other model is refused."""

    _provider: BaseLlm = PrivateAttr()

    def __init__(self, route: Route, provider: BaseLlm) -> None:
        super().__init__(model=route.model)
        self._provider = provider

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if llm_request.model not in (None, self.model):
            raise ModelNotAllowed(f"{llm_request.model} is not this installation's model")
        async for response in self._provider.generate_content_async(llm_request, stream):
            yield response


class FakeModels:
    """Providers for tests: every model answers ``reply`` (a text, or a function of the
    request) and records the requests it got and the route it served."""

    def __init__(self, reply: str | Callable[[LlmRequest], str]) -> None:
        self.reply = reply
        self.routes: list[Route] = []
        self.requests: list[LlmRequest] = []

    def __call__(self, route: Route) -> BaseLlm:
        return _FakeModel(route, self)


class _FakeModel(BaseLlm):
    _route: Route = PrivateAttr()
    _models: FakeModels = PrivateAttr()

    def __init__(self, route: Route, models: FakeModels) -> None:
        super().__init__(model=route.model)
        self._route = route
        self._models = models

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._models.routes.append(self._route)
        self._models.requests.append(llm_request)
        reply = self._models.reply
        text = reply if isinstance(reply, str) else reply(llm_request)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def vertex_models(project: str) -> Providers:
    """Vertex AI clients for a route, in the route's region."""

    def build(route: Route) -> BaseLlm:
        if route.provider == "gemini":
            client = genai.Client(vertexai=True, project=project, location=route.region)
            return Gemini(model=route.model, client=client)
        return Claude(
            model=route.model,
            client=AsyncAnthropicVertex(region=route.region, project_id=project),
        )

    return build


class ModelGateway:
    """The single way to a language model for one installation."""

    def __init__(self, pool: asyncpg.Pool, providers: Providers) -> None:
        self._pool = pool
        self._providers = providers

    async def route(self) -> Route:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(f"SELECT {_COLUMNS} FROM pxos.installation")
        if row is None:
            raise NoRoute("the installation has no route; it gets no model")
        return Route(**dict(row))

    async def adk_model(self) -> GatewayModel:
        """The model to give an ADK agent of this installation."""
        route = await self.route()
        return GatewayModel(route, self._providers(route))
