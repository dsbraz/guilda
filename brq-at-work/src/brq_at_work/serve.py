"""Run the BRQ@work installation: bootstrap it, then the kernel's service until SIGTERM.

    uv run python -m brq_at_work.serve

Everything comes from the deploy's environment (see .env.example): the databases, the
Temporal server, the context memory, the Vertex project and the model route.
"""

import os
from dataclasses import dataclass

from brq_at_work.composition import bootstrap, build_registry
from pxos_kernel import service
from pxos_kernel.channel import Card, Channel
from pxos_kernel.context import HindsightContextStore
from pxos_kernel.errors import PxosError
from pxos_kernel.ingestion import Connector
from pxos_kernel.model_access import ModelGateway, Route, vertex_models
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool

AGENT = "project-assistant"


class ChannelNotWired(PxosError):
    """No channel adapter is wired into the installation yet."""


class _Unwired:
    """Refuses every call, so the outbox keeps each card until a channel is wired."""

    async def deliver_card(self, card: Card) -> None:
        raise ChannelNotWired("no channel adapter is wired")

    async def post(self, thread: str, text: str) -> str:
        raise ChannelNotWired("no channel adapter is wired")


@dataclass(frozen=True)
class Adapters:
    connectors: tuple[Connector, ...]
    channel: Channel
    app: service.App | None
    """The channel's HTTP endpoint (the bot's), served behind the health check."""


def adapters() -> Adapters:
    """COMPOSITION POINT of the adapters: the SharePoint and Granola connectors, the Teams
    channel and the Teams bot's endpoint (packages under connectors/) are built here, from
    the deploy's environment, once they exist. Until then the installation syncs nothing,
    serves only its health check, and the channel refuses every card and post."""
    return Adapters(connectors=(), channel=_Unwired(), app=None)


async def serve(registry: Registry) -> None:
    pool = await create_pool(os.environ["PXOS_DATABASE_URL"])
    publisher = await create_pool(os.environ["PXOS_PUBLISHER_DATABASE_URL"])
    wired = adapters()
    try:
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
