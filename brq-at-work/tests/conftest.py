import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import retail_fixture.generated as retail_models
import yaml

from agents_testing import publish, scripted
from brq_at_work.composition import IDENTITIES, bootstrap, build_registry
from brq_at_work.scribe import DRAFT
from db_testing import Database, create_test_database
from pxos_kernel.model_access import FakeModels
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool
from temporal_testing import dev_server

RETAIL = Path(__file__).parents[2] / "pxos-kernel" / "tests" / "retail_fixture"


@pytest.fixture(scope="session")
def registry() -> Registry:
    """The BRQ@work composition plus the kernel's retail fixture, to run the same cycle."""
    registry = build_registry()
    registry.register(RETAIL, retail_models)
    return registry


@pytest.fixture(scope="session")
def database(registry: Registry, tmp_path_factory: pytest.TempPathFactory) -> Database:
    brq = yaml.safe_load(IDENTITIES.read_text(encoding="utf-8"))
    retail = yaml.safe_load((RETAIL / "identities.yaml").read_text(encoding="utf-8"))
    identities = tmp_path_factory.mktemp("identities") / "identities.yaml"
    identities.write_text(
        yaml.safe_dump(
            {
                "actors": brq["actors"] + retail["actors"],
                "delegations": brq["delegations"] + retail["delegations"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    database = create_test_database("pxos_test_brq")
    bootstrap(database.owner_dsn, registry, identities)

    async def published() -> None:
        pool = await create_pool(database.app_dsn)
        try:
            await publish(pool, registry, DRAFT, author="bruno", approver="eva")
        finally:
            await pool.close()

    asyncio.run(published())  # the scribe's first version, through the review
    return database


_COMMITMENT = re.compile(
    r"COMPROMISSO \| dono=(?P<owner>[^|\s]+) \| prazo=(?P<due>[^|\s]+)"
    r" \| frente=(?P<stream>[^|\s]+) \| descrição=(?P<description>[^|]+?)"
    r' \| trecho="(?P<quote>[^"]+)"'
)


def read_commitment(fact: str) -> dict[str, Any] | None:
    """What a model following the scribe's instructions answers for one fact."""
    match = _COMMITMENT.search(fact)
    if match is None:
        return None
    return {
        "payload": {
            "description": match["description"].strip(),
            "owner": match["owner"],
            "due_date": match["due"],
            "work_stream": match["stream"],
        },
        "quote": match["quote"],
    }


@pytest.fixture
def scribe_models() -> FakeModels:
    """The model behind the gateway in BRQ@work tests: it reads commitment lines."""
    return FakeModels(reply=scripted(read_commitment))


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.app_dsn)
    yield pool
    await pool.close()


@pytest.fixture(scope="session")
def temporal_address() -> Iterator[str]:
    with dev_server() as address:
        yield address
