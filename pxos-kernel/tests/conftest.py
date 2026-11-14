import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import retail_fixture.generated as retail_models

from db_testing import Database, create_test_database
from pxos_kernel.identity import install_identities, read_identities
from pxos_kernel.model_access import Residency, install_residency
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool, install_packages
from temporal_testing import dev_server

RETAIL = Path(__file__).parent / "retail_fixture"


@pytest.fixture(scope="session")
def registry() -> Registry:
    registry = Registry()
    registry.register(RETAIL, retail_models)
    return registry


def installation(name: str, registry: Registry, residency: Residency) -> Database:
    database = create_test_database(name)

    async def bootstrap() -> None:
        owner = await asyncpg.connect(database.owner_dsn)
        try:
            await install_identities(owner, read_identities(RETAIL / "identities.yaml"))
            await install_packages(owner, registry)
            await install_residency(owner, residency)
        finally:
            await owner.close()

    asyncio.run(bootstrap())
    return database


@pytest.fixture(scope="session")
def database(registry: Registry) -> Database:
    return installation("pxos_test_kernel", registry, "br")


@pytest.fixture(scope="session")
def other_database(registry: Registry) -> Database:
    """A second installation with the same actors, to show they share nothing (PXOS-63)."""
    return installation("pxos_test_other", registry, "global")


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.app_dsn)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def temporal_address() -> Iterator[str]:
    with dev_server() as address:
        yield address
