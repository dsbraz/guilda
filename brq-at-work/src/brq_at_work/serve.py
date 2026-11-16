"""Run the BRQ@work installation: bootstrap it, then the kernel's service until SIGTERM.

    uv run python -m brq_at_work.serve

Everything comes from the deploy's environment (see .env.example): the databases, the
Temporal server, the context memory, the Vertex project, the model route and the
SharePoint, Granola and Teams adapters.
"""

import os
import shelve
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg
import httpx
import yaml

from brq_at_work import environment
from brq_at_work.composition import IDENTITIES, bootstrap, build_registry
from brq_at_work.publish_agent import AGENT_FILE
from pxos_kernel import service
from pxos_kernel.channel import Channel
from pxos_kernel.context import HindsightContextStore
from pxos_kernel.identity import Identities, read_identities
from pxos_kernel.ingestion import Connector
from pxos_kernel.meetings import MeetingConnector
from pxos_kernel.model_access import ModelGateway, Route, vertex_models
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool
from pxos_teams import TeamsChannel, create_app

AGENT = "project-assistant"


@dataclass(frozen=True)
class Adapters:
    connectors: tuple[Connector, ...]
    channel: Channel
    app: service.App | None
    """The channel's HTTP endpoint (the bot's), served behind the health check."""


@asynccontextmanager
async def adapters(
    pool: asyncpg.Pool,
    registry: Registry,
    identities: Identities,
    env: Mapping[str, str] = os.environ,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[Adapters]:
    """COMPOSITION POINT of the adapters, from the deploy's environment: the SharePoint
    library and the Granola transcripts as connectors, Teams as the channel and the bot's
    endpoint. The assistant's thread (its definition file's ``thread``) is the project's
    Teams conversation. The message of each card is kept in a shelf at
    ``PXOS_TEAMS_CARDS_PATH``, which survives restarts; only the event loop touches it, so
    it needs no lock. The shelf and the bot's HTTP client close on the way out."""
    connectors = (
        environment.sharepoint(env),
        MeetingConnector(pool, environment.granola(env), agent=AGENT),
    )
    thread = yaml.safe_load(AGENT_FILE.read_text(encoding="utf-8"))["thread"]
    settings = environment.teams(env, thread)
    path = environment.required(env, "PXOS_TEAMS_CARDS_PATH")
    async with httpx.AsyncClient(timeout=30, transport=transport) as http:
        with shelve.open(path) as cards:
            yield Adapters(
                connectors=connectors,
                channel=TeamsChannel(settings, http, identities, cards=cards),
                app=create_app(settings, pool, registry, http),
            )


async def serve(registry: Registry) -> None:
    pool = await create_pool(os.environ["PXOS_DATABASE_URL"])
    publisher = await create_pool(os.environ["PXOS_PUBLISHER_DATABASE_URL"])
    try:
        async with adapters(pool, registry, read_identities(IDENTITIES)) as wired:
            await service.serve(
                temporal=os.environ["PXOS_TEMPORAL_ADDRESS"],
                task_queue=os.environ["PXOS_TASK_QUEUE"],
                agent=AGENT,
                pool=pool,
                publisher=publisher,
                store=HindsightContextStore.from_environment(),
                connectors=wired.connectors,
                gateway=ModelGateway(pool, vertex_models(os.environ["PXOS_VERTEX_PROJECT"])),
                registry=registry,
                channel=wired.channel,
                app=wired.app,
                port=int(os.environ.get("PXOS_HTTP_PORT", "8000")),
            )
    finally:
        await pool.close()
        await publisher.close()


def main() -> None:
    registry = build_registry()
    bootstrap(os.environ["PXOS_OWNER_DATABASE_URL"], registry, route=Route.from_environment())
    service.run(serve(registry))


if __name__ == "__main__":
    main()
