"""Own credentials per installation (PXOS-63).

Each installation has its own database and logins. Postgres roles are cluster-wide, so
what keeps one installation's login out of another's database is CONNECT: the kernel
migration revokes it from PUBLIC and the bootstrap grants it to that installation's
logins only.
"""

import asyncpg
import pytest

from db_testing import Database, on_database


async def test_a_login_of_one_installation_is_refused_by_another(
    database: Database, other_database: Database
) -> None:
    for ours, theirs in [(database, other_database), (other_database, database)]:
        for dsn in [ours.app_dsn, ours.publisher_dsn]:
            with pytest.raises(asyncpg.InsufficientPrivilegeError, match="permission denied"):
                await asyncpg.connect(on_database(dsn, theirs.name))


async def test_each_installation_opens_with_its_own_logins(other_database: Database) -> None:
    for dsn in [other_database.app_dsn, other_database.publisher_dsn]:
        connection = await asyncpg.connect(dsn)
        await connection.close()
