"""Ports for running the kernel's service in tests: a connector with nothing new, a context
memory that keeps nothing, a channel that records cards, and providers that are never
called. In this process, or in a subprocess stopped with SIGTERM:

    python served.py DSN PUBLISHER_DSN TEMPORAL_ADDRESS TASK_QUEUE PORT

serves until SIGTERM, then prints ``stopped``.
"""

import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import retail_fixture.generated as retail_models
from conftest import AGENT, RETAIL
from google.adk.models.base_llm import BaseLlm

from guilda_kernel import service
from guilda_kernel.agents import register_agents
from guilda_kernel.channel import Card
from guilda_kernel.context import Recalled
from guilda_kernel.ingestion import Changes
from guilda_kernel.model_access import ModelGateway, Route
from guilda_kernel.ontology import Registry
from guilda_kernel.state import create_pool


class Quiet:
    """A source system with nothing new."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def changes(self, cursor: str | None) -> Changes:
        return Changes(cursor="unchanged")


class Forgetful:
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        pass

    async def recall(self, query: str, *, tags: Sequence[str]) -> list[Recalled]:
        return []

    async def forget(self, source: str) -> None:
        pass


class Cards:
    def __init__(self) -> None:
        self.delivered: list[Card] = []

    async def deliver_card(self, card: Card) -> None:
        self.delivered.append(card)

    async def post(self, thread: str, text: str) -> str:
        return "message"


def kernel_registry() -> Registry:
    registry = Registry()
    registry.register(RETAIL, retail_models)
    register_agents(registry)
    return registry


def no_model(route: Route) -> BaseLlm:
    raise AssertionError("no test of the service asks for a model")


async def endpoint(
    scope: dict[str, Any],
    receive: Callable[[], Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """The distribution's endpoint: answers every request with 204."""
    if scope["type"] == "http":
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def serve(
    dsn: str,
    publisher_dsn: str,
    address: str,
    queue: str,
    port: int,
    *,
    registry: Registry,
    connector: Quiet,
    channel: Cards,
) -> None:
    pool = await create_pool(dsn)
    publisher = await create_pool(publisher_dsn)
    try:
        await service.serve(
            temporal=address,
            task_queue=queue,
            agent=AGENT,
            pool=pool,
            publisher=publisher,
            store=Forgetful(),
            connectors=[connector],
            gateway=ModelGateway(pool, no_model),
            registry=registry,
            channel=channel,
            app=endpoint,
            port=port,
            poll_interval=0.05,
        )
    finally:
        await pool.close()
        await publisher.close()


if __name__ == "__main__":
    dsn, publisher_dsn, address, queue, port = sys.argv[1:6]
    service.run(
        serve(
            dsn,
            publisher_dsn,
            address,
            queue,
            int(port),
            registry=kernel_registry(),
            connector=Quiet("quiet"),
            channel=Cards(),
        )
    )
    print("stopped", flush=True)
