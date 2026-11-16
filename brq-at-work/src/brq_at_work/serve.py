"""Run the BRQ@work installation: bootstrap it, then the kernel's service until SIGTERM.

    uv run python -m brq_at_work.serve

Everything comes from the deploy's environment (see .env.example): the databases, the
Temporal server, the context memory, the Vertex project, the model route and the
SharePoint, Granola and Teams adapters.
"""

import os
import shelve
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

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
from pxos_telegram import TelegramChannel
from pxos_telegram import create_app as create_telegram_app

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
    library and the Granola transcripts as connectors, and the channel ``PXOS_CHANNEL``
    picks with its app: Teams and the bot's endpoint, or, for local development, Telegram
    and its long poll. The assistant's thread (its definition file's ``thread``) is the
    project's Teams conversation or Telegram group. The message of each card is kept in a
    shelf at ``PXOS_TEAMS_CARDS_PATH`` or ``PXOS_TELEGRAM_CARDS_PATH``, which survives
    restarts; only the event loop touches it, so it needs no lock. The shelf and the bot's
    HTTP client close on the way out."""
    connectors = (
        environment.sharepoint(env),
        MeetingConnector(pool, environment.granola(env), agent=AGENT),
    )
    thread = yaml.safe_load(AGENT_FILE.read_text(encoding="utf-8"))["thread"]
    path, wire = _channel(env, thread, pool, registry, identities)
    async with httpx.AsyncClient(timeout=30, transport=transport) as http:
        with shelve.open(path) as cards:
            channel, app = wire(http, cards)
            yield Adapters(connectors=connectors, channel=channel, app=app)


Shelf = shelve.Shelf[Any]
Wired = tuple[Channel, service.App]


def _channel(
    env: Mapping[str, str],
    thread: str,
    pool: asyncpg.Pool,
    registry: Registry,
    identities: Identities,
) -> tuple[str, Callable[[httpx.AsyncClient, Shelf], Wired]]:
    """The chosen channel's cards path, and how to build it and its app."""
    if environment.channel(env) == "telegram":
        bot = environment.telegram(env, thread)

        def telegram(http: httpx.AsyncClient, cards: Shelf) -> Wired:
            channel = TelegramChannel(bot, http, identities, cards=cards)
            return channel, create_telegram_app(bot, pool, registry, http, cards)

        return environment.required(env, "PXOS_TELEGRAM_CARDS_PATH"), telegram
    settings = environment.teams(env, thread)

    def teams(http: httpx.AsyncClient, cards: Shelf) -> Wired:
        channel = TeamsChannel(settings, http, identities, cards=cards)
        return channel, create_app(settings, pool, registry, http)

    return environment.required(env, "PXOS_TEAMS_CARDS_PATH"), teams


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
