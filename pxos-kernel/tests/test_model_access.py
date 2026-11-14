"""Model access follows the installation's residency (PXOS-72, PRD §6.4, ADR 0004 §2).

Each installation records the residency it requires. The kernel derives a single route
from it, and the gateway builds model clients for that route only: an installation that
requires residency in Brazil never gets a client for the global endpoint.
"""

from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest

from db_testing import Database, create_test_database
from pxos_kernel.model_access import (
    ROUTES,
    ModelGateway,
    NoResidency,
    ResidencyMismatch,
    install_residency,
    vertex_models,
)
from pxos_kernel.state import create_pool


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


def test_the_br_route_stays_in_sao_paulo() -> None:
    route = ROUTES["br"]
    assert route.provider == "gemini"
    assert route.region == route.embedding_region == "southamerica-east1"


async def test_each_installation_gets_its_own_route(
    pool: asyncpg.Pool, other_pool: asyncpg.Pool
) -> None:
    assert await ModelGateway(pool, vertex_models("p")).route() == ROUTES["br"]
    assert await ModelGateway(other_pool, vertex_models("p")).route() == ROUTES["global"]


async def test_a_br_installation_never_gets_a_client_for_the_global_endpoint(
    pool: asyncpg.Pool,
) -> None:
    model = await ModelGateway(pool, vertex_models("p")).provider()
    assert endpoint_host(model) == "southamerica-east1-aiplatform.googleapis.com"


async def test_a_global_installation_uses_claude_on_the_global_endpoint(
    other_pool: asyncpg.Pool,
) -> None:
    model = await ModelGateway(other_pool, vertex_models("p")).provider()
    assert model.model == ROUTES["global"].model
    assert endpoint_host(model) == "aiplatform.googleapis.com"


@pytest.fixture(scope="module")
def unconfigured() -> Database:
    return create_test_database("pxos_test_no_residency")


async def test_an_installation_without_residency_gets_no_model(unconfigured: Database) -> None:
    pool = await create_pool(unconfigured.app_dsn)
    try:
        with pytest.raises(NoResidency):
            await ModelGateway(pool, vertex_models("p")).route()
    finally:
        await pool.close()


async def test_the_application_reads_the_residency_but_cannot_change_it(
    pool: asyncpg.Pool,
) -> None:
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT residency FROM pxos.installation") == "br"
        for statement in (
            "UPDATE pxos.installation SET residency = 'global'",
            "DELETE FROM pxos.installation",
            "INSERT INTO pxos.installation (residency) VALUES ('global')",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)


async def test_the_residency_is_recorded_once_and_only_with_a_known_value(
    database: Database,
) -> None:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        await install_residency(owner, "br")  # bootstrapping again changes nothing
        with pytest.raises(ResidencyMismatch):
            await install_residency(owner, "global")
        assert await owner.fetchval("SELECT residency FROM pxos.installation") == "br"
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute("UPDATE pxos.installation SET residency = 'us'")
    finally:
        await owner.close()
