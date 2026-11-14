"""Registered packages in the database (PXOS-49, PXOS-82): approvers, reversibility, module
rules and readers per type."""

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
                INSERT INTO pxos.package_actions
                    (package, digest, action, creates, approver_role, reversible)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (package, digest, action) DO UPDATE SET reversible = EXCLUDED.reversible
                """,
                [
                    (package.name, package.digest, a.name, a.creates, a.approver, a.reversible)
                    for a in package.actions
                ],
            )
            await owner.executemany(
                """
                INSERT INTO pxos.package_rules (package, digest, action, parameter, operator, value)
                VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT DO NOTHING
                """,
                [
                    (package.name, package.digest, a.name, r.parameter, r.operator, r.value)
                    for a in package.actions
                    for r in a.rules
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
