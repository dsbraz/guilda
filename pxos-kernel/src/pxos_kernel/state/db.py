"""Connection pool for the application role and the logins allowed to connect."""

import json

import asyncpg


async def _codecs(connection: asyncpg.Connection) -> None:
    for name in ("json", "jsonb"):
        await connection.set_type_codec(
            name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def create_pool(dsn: str) -> asyncpg.Pool:
    """Pool whose connections exchange json/jsonb as Python values."""
    return await asyncpg.create_pool(dsn, init=_codecs)


async def grant_connect(owner: asyncpg.Connection, *logins: str) -> None:
    """Let ``logins`` connect to this installation's database, and nobody else (PXOS-63)."""
    for login in logins:
        statement = await owner.fetchval(
            "SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), $1::text)",
            login,
        )
        await owner.execute(statement)
