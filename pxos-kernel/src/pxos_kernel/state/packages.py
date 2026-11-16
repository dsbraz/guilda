"""Registered packages in the database (PXOS-49): approvers and readers."""

import asyncpg

from pxos_kernel.ontology import Registry


async def install_packages(owner: asyncpg.Connection, registry: Registry) -> None:
    """Record the registered packages so the database knows approvers and readers."""
    async with owner.transaction():
        for registered in registry:
            package = registered.package
            await owner.execute(
                """
                INSERT INTO pxos.packages (name, version, digest) VALUES ($1, $2, $3)
                ON CONFLICT (name) DO UPDATE
                SET version = EXCLUDED.version, digest = EXCLUDED.digest
                """,
                package.name,
                package.version,
                package.digest,
            )
            await owner.executemany(
                """
                INSERT INTO pxos.package_actions (package, digest, action, creates, approver_role)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
                """,
                [
                    (package.name, package.digest, a.name, a.creates, a.approver)
                    for a in package.actions
                ],
            )
            await owner.execute("DELETE FROM pxos.type_readers WHERE package = $1", package.name)
            await owner.executemany(
                "INSERT INTO pxos.type_readers (package, type_name, role) VALUES ($1, $2, $3)",
                [
                    (package.name, type_name, role)
                    for type_name, roles in package.readers.items()
                    for role in roles
                ],
            )
