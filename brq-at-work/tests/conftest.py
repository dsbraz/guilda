from collections.abc import AsyncIterator

import asyncpg
import pytest

from brq_at_work.composition import bootstrap, build_registry
from db_testing import Database, create_test_database
from model_testing import ROUTE
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool


@pytest.fixture(scope="session")
def registry() -> Registry:
    return build_registry()


@pytest.fixture(scope="session")
def database(registry: Registry) -> Database:
    database = create_test_database("pxos_test_brq")
    bootstrap(database.owner_dsn, registry, route=ROUTE)
    return database


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.app_dsn)
    yield pool
    await pool.close()
