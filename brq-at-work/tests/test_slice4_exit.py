"""Exit criterion of slice 4 (PXOS-64): a client's contact and another installation stay out.

Two BRQ@work installations run side by side, each with its own database logins. Dora is
the client's contact, an external actor of this installation: she reaches no internal
context.
"""

import asyncpg
import pytest

from brq_at_work.composition import bootstrap
from db_testing import Database, create_test_database, on_database
from pxos_kernel.identity import NotAuthorized, session
from pxos_kernel.ontology import Registry


@pytest.fixture(scope="module")
def other_installation(registry: Registry) -> Database:
    database = create_test_database("pxos_test_brq_other")
    bootstrap(database.owner_dsn, registry)
    return database


async def test_a_client_contact_and_another_installation_stay_out(
    database: Database, other_installation: Database, pool: asyncpg.Pool
) -> None:
    # 1: the kernel refuses to open a session for Dora, because she is external.
    with pytest.raises(NotAuthorized, match="dora is external"):
        async with session(pool, "dora"):
            pass

    # 2: by raw SQL, the database refuses her session as well. Every policy on the
    # canonical tables goes through current_actor(), so this is the refusal any read or
    # write of hers meets (the kernel tests read the tables themselves).
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SELECT set_config('pxos.actor', 'dora', true)")
        with pytest.raises(asyncpg.PostgresError, match="dora is external"):
            await connection.fetchval("SELECT pxos.current_actor()")

    # 3: the other installation's database logins do not open this database.
    for dsn in [other_installation.app_dsn, other_installation.publisher_dsn]:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await asyncpg.connect(on_database(dsn, database.name))

    # 4: Ana still works in her own installation.
    async with session(pool, "ana") as ana:
        assert await ana.fetchval("SELECT pxos.current_actor()") == "ana"
