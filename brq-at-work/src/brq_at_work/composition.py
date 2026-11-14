"""Composition root: BRQ@work registers its packages in the kernel (ADR 0003 §1)."""

import asyncio
from pathlib import Path

import asyncpg

import brq_at_work.ontology.work_context.generated as work_context_models
from pxos_kernel.identity import install_identities, read_identities
from pxos_kernel.model_access import Residency, install_residency
from pxos_kernel.ontology import Registry
from pxos_kernel.state import install_packages, upgrade

ROOT = Path(__file__).resolve().parent
WORK_CONTEXT = ROOT / "ontology" / "work_context"
IDENTITIES = ROOT / "identities.yaml"


def build_registry() -> Registry:
    registry = Registry()
    registry.register(WORK_CONTEXT, work_context_models)
    return registry


RESIDENCY: Residency = "br"
"""BRQ@work handles client transcripts: unless an installation says otherwise, models and
memory stay in Brazil."""


def bootstrap(
    owner_dsn: str,
    registry: Registry,
    identities: Path = IDENTITIES,
    *,
    residency: Residency = RESIDENCY,
) -> None:
    """Migrate the installation and record identities, packages and residency, as the owner."""
    upgrade(owner_dsn)

    async def install() -> None:
        owner = await asyncpg.connect(owner_dsn)
        try:
            await install_identities(owner, read_identities(identities))
            await install_packages(owner, registry)
            await install_residency(owner, residency)
        finally:
            await owner.close()

    asyncio.run(install())
