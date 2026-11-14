"""Model access by residency (PXOS-72, K11; PRD §6.4; ADR 0004 §2, rule 1).

Every installation records the residency it requires, and the kernel derives a single
route from it: the provider, model and region every model call of that installation
uses, and where embeddings are computed. The gateway builds clients for that route only;
nobody asks it for a model or a region.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import asyncpg
from anthropic import AsyncAnthropicVertex
from google import genai
from google.adk.models.anthropic_llm import Claude
from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini

from pxos_kernel.errors import PxosError

Residency = Literal["br", "global"]


class NoResidency(PxosError):
    pass


class ResidencyMismatch(PxosError):
    pass


@dataclass(frozen=True)
class Route:
    residency: Residency
    provider: Literal["gemini", "claude"]
    model: str
    region: str
    embedding_model: str
    embedding_region: str


ROUTES: dict[Residency, Route] = {
    # Customer data that must stay in the country goes to Gemini in São Paulo.
    "br": Route(
        residency="br",
        provider="gemini",
        model="gemini-3.8-flash",
        region="southamerica-east1",
        embedding_model="gemini-embedding-001",
        embedding_region="southamerica-east1",
    ),
    # Everything else may use Claude on the global endpoint.
    "global": Route(
        residency="global",
        provider="claude",
        model="claude-sonnet-5-5",
        region="global",
        embedding_model="gemini-embedding-001",
        embedding_region="us-central1",
    ),
}

Providers = Callable[[Route], BaseLlm]
"""Builds the model client for a route."""


async def install_residency(owner: asyncpg.Connection, residency: Residency) -> None:
    """Record the installation's residency, as the owner. Bootstrapping again with the same
    residency changes nothing; another residency is refused."""
    await owner.execute(
        "INSERT INTO pxos.installation (residency) VALUES ($1) ON CONFLICT DO NOTHING",
        residency,
    )
    recorded = await owner.fetchval("SELECT residency FROM pxos.installation")
    if recorded != residency:
        raise ResidencyMismatch(f"installation requires residency {recorded}, not {residency}")


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
            residency = await connection.fetchval("SELECT residency FROM pxos.installation")
        if residency is None:
            raise NoResidency("the installation has no residency; it gets no model")
        return ROUTES[residency]

    async def provider(self) -> BaseLlm:
        return self._providers(await self.route())
