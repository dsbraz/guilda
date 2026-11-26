"""Model access by the installation's route (K72, K11; PRD §6.4; ADR 0004 §2, rule 1).

Which provider, model and regions an installation uses is decided at deploy: the FDE sets
them per client in the environment (see .env.example), and the bootstrap records them as
the installation's single route. Every model call of that installation uses it, and the
gateway builds clients for that route only; nobody asks it for a model or a region. The
kernel fixes no model, no region and no price: the deploy also sets what the model costs
per million tokens, and the gateway prices the tokens each model it issued used.
"""

import os
import weakref
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import astuple, dataclass, fields
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Self, get_args

import asyncpg
from anthropic import AsyncAnthropicVertex
from google import genai
from google.adk.models.anthropic_llm import Claude
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr

from guilda_kernel.errors import GuildaError

Provider = Literal["gemini", "claude"]
"""The providers the kernel has a client for."""


class RouteNotConfigured(GuildaError):
    pass


class NoRoute(GuildaError):
    pass


class ModelNotAllowed(GuildaError):
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
    input_price: Decimal
    """What the model costs per million input tokens."""
    output_price: Decimal
    """What the model costs per million output tokens."""

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
        for price in ("input_price", "output_price"):
            values[price] = _price(_VARIABLES[price], values[price])
        if values["provider"] not in get_args(Provider):
            raise RouteNotConfigured(
                f"GUILDA_MODEL_PROVIDER must be one of {', '.join(get_args(Provider))}"
            )
        return cls(**values)


_VARIABLES = {
    "provider": "GUILDA_MODEL_PROVIDER",
    "model": "GUILDA_MODEL",
    "region": "GUILDA_MODEL_REGION",
    "memory_model": "GUILDA_MEMORY_MODEL",
    "memory_region": "GUILDA_MEMORY_REGION",
    "embedding_model": "GUILDA_EMBEDDING_MODEL",
    "embedding_region": "GUILDA_EMBEDDING_REGION",
    "input_price": "GUILDA_MODEL_INPUT_PRICE",
    "output_price": "GUILDA_MODEL_OUTPUT_PRICE",
}


def _price(name: str, value: str) -> Decimal:
    try:
        price = Decimal(value)
    except InvalidOperation:
        price = Decimal(-1)
    if not price.is_finite() or price < 0:
        raise RouteNotConfigured(f"{name} must be a non-negative number")
    return price


_COLUMNS = ", ".join(field.name for field in fields(Route))
_PLACEHOLDERS = ", ".join(f"${n}" for n in range(1, len(fields(Route)) + 1))

Providers = Callable[[Route], BaseLlm]
"""Builds the model client for a route."""


async def install_route(owner: asyncpg.Connection, route: Route) -> None:
    """Record the installation's route, as the owner. A new deploy replaces it."""
    updates = ", ".join(f"{f.name} = EXCLUDED.{f.name}" for f in fields(Route))
    await owner.execute(
        f"INSERT INTO guilda.installation ({_COLUMNS}) VALUES ({_PLACEHOLDERS})"
        f" ON CONFLICT (singleton) DO UPDATE SET {updates}",
        *astuple(route),
    )


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal
    """The tokens priced by the route, in the currency the deploy set the prices in."""


class GatewayModel(BaseLlm):
    """The ADK model an agent gets from the gateway: every call goes to the route's
    provider, a request for any other model is refused, and the tokens used are counted."""

    _route: Route = PrivateAttr()
    _provider: BaseLlm = PrivateAttr()
    _tokens: list[int] = PrivateAttr()

    def __init__(self, route: Route, provider: BaseLlm) -> None:
        super().__init__(model=route.model)
        self._route = route
        self._provider = provider
        self._tokens = [0, 0]

    def usage(self) -> Usage:
        """The tokens this model used so far, and their cost."""
        input_tokens, output_tokens = self._tokens
        cost = input_tokens * self._route.input_price + output_tokens * self._route.output_price
        return Usage(self.model, input_tokens, output_tokens, cost / 1_000_000)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if llm_request.model not in (None, self.model):
            raise ModelNotAllowed(f"{llm_request.model} is not this installation's model")
        async for response in self._provider.generate_content_async(llm_request, stream):
            if response.usage_metadata is not None:
                self._tokens[0] += response.usage_metadata.prompt_token_count or 0
                self._tokens[1] += response.usage_metadata.candidates_token_count or 0
            yield response


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
            row = await connection.fetchrow(f"SELECT {_COLUMNS} FROM guilda.installation")
        if row is None:
            raise NoRoute("the installation has no route; it gets no model")
        return Route(**dict(row))

    async def adk_model(self) -> GatewayModel:
        """The model to give an ADK agent of this installation."""
        route = await self.route()
        model = GatewayModel(route, self._providers(route))
        _issued[id(model)] = model
        return model


_issued: weakref.WeakValueDictionary[int, GatewayModel] = weakref.WeakValueDictionary()
"""The models gateways issued and still in use; a model built by hand is not among them."""


def issued_by_gateway(model: BaseLlm) -> bool:
    return _issued.get(id(model)) is model
