import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import retail_fixture.generated as retail_models

from agents_testing import publish
from db_testing import Database, create_test_database
from guilda_kernel.agents import register_agents
from guilda_kernel.identity import install_identities, read_identities
from guilda_kernel.model_access import Route, install_route
from guilda_kernel.ontology import Registry
from guilda_kernel.state import create_pool, install_packages
from model_testing import OTHER_ROUTE, ROUTE
from temporal_testing import dev_server

RETAIL = Path(__file__).parent / "retail_fixture"


@pytest.fixture(scope="session")
def registry() -> Registry:
    registry = Registry()
    registry.register(RETAIL, retail_models)
    register_agents(registry)
    return registry


def installation(name: str, registry: Registry, route: Route) -> Database:
    database = create_test_database(name)

    async def bootstrap() -> None:
        owner = await asyncpg.connect(database.owner_dsn)
        try:
            await install_identities(owner, read_identities(RETAIL / "identities.yaml"))
            await install_packages(owner, registry)
            await install_route(owner, route)
        finally:
            await owner.close()

    asyncio.run(bootstrap())
    return database


AGENT = "restock-agent"
MEMORY_INSTRUCTIONS = "Extract every restock promise, one per fact, with the verbatim sentence."
RESTOCK_AGENT = {
    "agent": AGENT,
    # Braces are text, not a template: the kernel hands instructions to ADK literally.
    "instructions": "Read the restock promise: its {sku}, quantity and verbatim quote.",
    "memory_instructions": MEMORY_INSTRUCTIONS,
    "tools": ["recall"],
    "recall_tags": ["store:aisle-3"],
    "schedule": "0 9 * * 1",
    "answer_deadline_hours": 24,
    "max_rounds": 2,
    "thread": "thread:aisle-3",
    "deliverable": "retail.create_restock_order",
}


@pytest.fixture(scope="session")
def database(registry: Registry) -> Database:
    """The kernel's installation, where the restock agent has an approved version."""
    database = installation("guilda_test_kernel", registry, ROUTE)

    async def published() -> None:
        pool = await create_pool(database.app_dsn)
        try:
            await publish(pool, registry, RESTOCK_AGENT, author="gina", approver="paula")
        finally:
            await pool.close()

    asyncio.run(published())
    return database


@pytest.fixture(scope="session")
def other_database(registry: Registry) -> Database:
    """A second installation with the same actors, to show they share nothing (K63)."""
    return installation("guilda_test_other", registry, OTHER_ROUTE)


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.app_dsn)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def temporal_address() -> Iterator[str]:
    with dev_server() as address:
        yield address
