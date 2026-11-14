"""Exit criterion of slice 4 (PXOS-64): a client's contact and another installation stay out.

Two BRQ@work installations run side by side, each with its own database logins and its
own identity provider. Dora is the client's contact, an external actor of this
installation: she has a valid token and still reaches no internal context.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from starlette.applications import Starlette

from brq_at_work.composition import bootstrap
from db_testing import Database, create_test_database, on_database
from mcp_testing import BASE, call, post_status, running
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import build_server, http_app
from pxos_kernel.ontology import Registry


@pytest.fixture(scope="module")
def other_installation(registry: Registry) -> Database:
    database = create_test_database("pxos_test_brq_other")
    bootstrap(database.owner_dsn, registry)
    return database


@pytest.fixture(scope="module")
def idp() -> LocalIssuer:
    return LocalIssuer.generate(issuer="http://idp.brq.test", audience="brq-at-work")


@pytest.fixture
async def app(pool: asyncpg.Pool, registry: Registry, idp: LocalIssuer) -> AsyncIterator[Starlette]:
    app = http_app(
        build_server(pool, registry, name="brq-at-work"),
        JwtVerifier(issuer=idp.issuer, audience=idp.audience, jwks=idp.jwks()),
        pool=pool,
        issuer_url=idp.issuer,
        resource_url=f"{BASE}/mcp",
    )
    async with running(app):
        yield app


async def test_a_client_contact_and_another_installation_stay_out(
    database: Database,
    other_installation: Database,
    pool: asyncpg.Pool,
    app: Starlette,
    idp: LocalIssuer,
) -> None:
    # 1: Dora's token is valid for this installation, and the MCP still refuses her.
    assert await post_status(app, idp.token("dora")) == 401

    # 2: by raw SQL, the database refuses her session because she is external. Every
    # policy on the canonical tables goes through current_actor(), so this is the refusal
    # any read or write of hers meets (the kernel tests read the tables themselves).
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT set_config('pxos.actor', 'dora', true)")
        with pytest.raises(asyncpg.PostgresError, match="dora is external"):
            await connection.fetchval("SELECT pxos.current_actor()")

    # 3: Ana's token from the other installation's identity provider is refused here.
    theirs = LocalIssuer.generate(issuer="http://idp.other.test", audience="brq-at-work")
    assert await post_status(app, theirs.token("ana")) == 401

    # 4: the other installation's database logins do not open this database.
    for dsn in [other_installation.app_dsn, other_installation.publisher_dsn]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await asyncpg.connect(on_database(dsn, database.name))

    # 5: Ana still lists the commitments of her own installation.
    assert "items" in await call(app, idp.token("ana"), "list_commitments", {})
