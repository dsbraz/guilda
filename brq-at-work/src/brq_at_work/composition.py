"""Composition root: BRQ@work registers its packages in the kernel (ADR 0003 §1)."""

import asyncio
from pathlib import Path

import asyncpg

from brq_at_work.ontology import work_context_models
from pxos_kernel.agents import register_agents
from pxos_kernel.identity import install_identities, read_identities
from pxos_kernel.model_access import Route, install_route
from pxos_kernel.ontology import Registry
from pxos_kernel.state import install_packages, upgrade

ROOT = Path(__file__).resolve().parent
WORK_CONTEXT = ROOT / "ontology" / "work_context"
IDENTITIES = ROOT / "identities.yaml"


def build_registry() -> Registry:
    registry = Registry()
    registry.register(WORK_CONTEXT, work_context_models)
    register_agents(registry)
    return registry


def bootstrap(
    owner_dsn: str,
    registry: Registry,
    identities: Path = IDENTITIES,
    *,
    route: Route | None = None,
) -> None:
    """Migrate the installation and record identities, packages and, when the deploy
    configures one, the model route (``Route.from_environment()``), as the owner.
    Without a route the installation gets no model."""
    upgrade(owner_dsn)

    async def install() -> None:
        owner = await asyncpg.connect(owner_dsn)
        try:
            await install_identities(owner, read_identities(identities))
            await install_packages(owner, registry)
            if route is not None:
                await install_route(owner, route)
        finally:
            await owner.close()

    asyncio.run(install())
