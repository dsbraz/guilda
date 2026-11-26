"""The kernel's service: one process runs an installation until it is stopped (ADR 0004).

It holds the Temporal worker of the kernel workflows, the outbox publisher with the
cycle's handlers and an HTTP server for the distribution's endpoint, if it gives one,
beside a health check. The distribution passes in its connectors, channel and registry,
so the kernel imports no adapter. When any part fails, the others stop and the process
exits, for the supervisor to start it again.
"""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any

import asyncpg
import uvicorn
from temporalio.client import Client
from temporalio.worker import Worker

from guilda_kernel.channel import Channel
from guilda_kernel.context import ContextStore
from guilda_kernel.ingestion import Connector
from guilda_kernel.model_access import ModelGateway
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Handler, publish
from guilda_kernel.workflows import (
    AgentCycle,
    CycleActivities,
    SyncActivities,
    SyncConnector,
    cycle_handlers,
    workflow_runner,
)

App = Callable[..., Awaitable[None]]
"""An ASGI application."""
HEALTH = "/healthz"

log = logging.getLogger(__name__)


async def publish_forever(
    publisher: asyncpg.Pool, handlers: Mapping[str, Handler], *, interval: float
) -> None:
    """Deliver the outbox every ``interval`` seconds until cancelled. A failing handler
    leaves its event for the next pass; the failure is logged by its type alone, because
    its message may quote a source."""
    while True:
        try:
            await publish(publisher, handlers)
        except Exception as error:
            log.warning("outbox publish failed: %s", type(error).__name__)
        await asyncio.sleep(interval)


def with_health(app: App | None) -> App:
    """``app`` behind a health check at ``HEALTH``; without an app, nothing else answers."""

    async def routed(
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] == "http" and scope["path"] == HEALTH:
            await _respond(send, 200, b"ok")
        elif app is not None:
            await app(scope, receive, send)
        elif scope["type"] == "http":
            await _respond(send, 404, b"")

    return routed


async def _respond(
    send: Callable[[dict[str, Any]], Awaitable[None]], status: int, body: bytes
) -> None:
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": body})


class _Server(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield  # the service owns the signals and stops the server by cancelling it


async def serve_http(app: App, port: int) -> None:
    """Serve ``app`` on ``port`` until cancelled, then finish the requests in flight."""
    server = _Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning"))
    serving = asyncio.ensure_future(server.serve())
    try:
        await asyncio.shield(serving)
    finally:
        server.should_exit = True
        await serving


async def serve(
    *,
    temporal: str,
    task_queue: str,
    agent: str,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    store: ContextStore,
    connectors: Iterable[Connector],
    gateway: ModelGateway,
    registry: Registry,
    channel: Channel,
    namespace: str = "default",
    app: App | None = None,
    port: int = 8000,
    poll_interval: float = 1.0,
) -> None:
    """Run ``agent``'s installation until cancelled. ``pool`` is the application login's,
    ``publisher`` the outbox publisher's; ``temporal`` is the server's host:port."""
    client = await Client.connect(temporal, namespace=namespace)
    connectors = list(connectors)
    steps = CycleActivities(pool, store, connectors, gateway, registry, channel)
    syncing = SyncActivities(pool, store, connectors, agent=agent)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentCycle, SyncConnector],
        activities=[*steps.registered(), *syncing.registered()],
        workflow_runner=workflow_runner(),
    )
    handlers = cycle_handlers(
        pool, registry, store, channel, client, agent=agent, task_queue=task_queue
    )
    async with asyncio.TaskGroup() as parts:
        parts.create_task(worker.run())
        parts.create_task(publish_forever(publisher, handlers, interval=poll_interval))
        parts.create_task(serve_http(with_health(app), port))


def run(main: Coroutine[Any, Any, None]) -> None:
    """Run ``main`` until it returns or SIGTERM cancels it, which stops it gracefully."""

    async def until_terminated() -> None:
        task = asyncio.ensure_future(main)
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(until_terminated())
