"""Shared test database helpers for pxos-kernel and brq-at-work tests.

Integration tests need the Compose Postgres (or PXOS_TEST_DATABASE_URL). They fail, not
skip, when it is absent. The schema is migrated by the owner; tests connect as logins
of that database only, members of pxos_app, so RLS and grants are what production sees.
Each database stands for an installation and has its own logins (PXOS-63).
"""

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from pxos_kernel.identity import Connection
from pxos_kernel.state import Fact, Handler, grant_connect, publish, upgrade

ADMIN_DSN = os.environ.get(
    "PXOS_TEST_DATABASE_URL", "postgresql://pxos:pxos-local@127.0.0.1:25432/postgres"
)
PASSWORD = "pxos-test"


@dataclass(frozen=True)
class Database:
    name: str
    owner_dsn: str
    app_dsn: str
    publisher_dsn: str


def _with(dsn: str, *, database: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname or "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    user = user or parts.username or ""
    password = password if password is not None else (parts.password or "")
    credentials = f"{user}:{password}@" if password else f"{user}@"
    return urlunsplit((parts.scheme, f"{credentials}{host}{port}", f"/{database}", "", ""))


def create_test_database(name: str) -> Database:
    """Recreate ``name``, migrate it and make sure the application and publisher logins exist."""

    async def recreate() -> None:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()

    async def logins(owner_dsn: str) -> None:
        owner = await asyncpg.connect(owner_dsn)
        try:
            for login, role in [
                (app_login(name), "pxos_app"),
                (publisher_login(name), "pxos_publisher"),
            ]:
                await owner.execute(
                    f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{login}') THEN
                            CREATE ROLE {login} LOGIN PASSWORD '{PASSWORD}'
                                NOSUPERUSER NOBYPASSRLS IN ROLE {role};
                        END IF;
                    END $$
                    """
                )
            await grant_connect(owner, app_login(name), publisher_login(name))
        finally:
            await owner.close()

    asyncio.run(recreate())
    database = existing_database(name)
    upgrade(database.owner_dsn)
    asyncio.run(logins(database.owner_dsn))
    return database


def existing_database(name: str) -> Database:
    """Connection strings of a database created by ``create_test_database``."""
    return Database(
        name=name,
        owner_dsn=_with(ADMIN_DSN, database=name),
        app_dsn=_with(ADMIN_DSN, database=name, user=app_login(name), password=PASSWORD),
        publisher_dsn=_with(
            ADMIN_DSN, database=name, user=publisher_login(name), password=PASSWORD
        ),
    )


def app_login(name: str) -> str:
    return f"{name}_app"


def publisher_login(name: str) -> str:
    return f"{name}_publisher"


def on_database(dsn: str, name: str) -> str:
    """``dsn`` with the same login, pointed at database ``name``."""
    return _with(dsn, database=name)


async def drain(publisher: asyncpg.Pool, handlers: Mapping[str, Handler]) -> int:
    """Publish the pending events; a publisher that never empties the outbox fails the
    test instead of hanging it."""
    async with asyncio.timeout(30):
        return await publish(publisher, handlers)


async def history(connection: Connection, entity_id: object) -> list[Fact]:
    """Every revision of an entity the session can read, oldest first."""
    rows = await connection.fetch(
        "SELECT * FROM pxos.facts WHERE entity_id = $1 ORDER BY revision", entity_id
    )
    return [Fact(**dict(row)) for row in rows]
