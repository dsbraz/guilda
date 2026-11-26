"""Composition root: BRQ@work registers its packages in the kernel (ADR 0003 §1)."""

import asyncio
from pathlib import Path

import asyncpg

from brq_at_work.weekly_report import models as weekly_report
from guilda_kernel.agents import register_agents
from guilda_kernel.identity import install_identities, read_identities
from guilda_kernel.model_access import Route, install_route
from guilda_kernel.ontology import Registry
from guilda_kernel.state import install_packages, upgrade

ROOT = Path(__file__).resolve().parent
IDENTITIES = ROOT / "identities.yaml"
WEEKLY_REPORT = "brq_weekly_report"
ACCESS = ("corporate",)
"""The access tags of the corporate base: every connector's sources carry them, and the
project assistant's recall_tags (project_assistant.yaml) are the same."""


def build_registry() -> Registry:
    registry = Registry()
    register_agents(registry)
    registry.register(ROOT / "weekly_report", weekly_report)
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
