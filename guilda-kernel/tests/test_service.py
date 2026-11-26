"""The kernel's service runs an installation until it is stopped (ADR 0004).

One process holds the worker of the kernel workflows, the outbox publisher with the
cycle's handlers and, when the distribution gives one, its HTTP endpoint, beside a health
check. A handler that fails leaves its event for the next pass and stops nothing; SIGTERM
stops the service gracefully.
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from served import Cards, Quiet, serve
from temporalio.client import Client
from test_outbox import proposed

from db_testing import Database, drain
from guilda_kernel import service
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Event, create_pool
from guilda_kernel.workflows import SyncConnector, sync_workflow_id

SERVED = Path(__file__).with_name("served.py")
ROOT = Path(__file__).resolve().parents[2]
SECRET = "restock aisle 3 with 12 boxes"


@pytest.fixture
async def publisher(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.publisher_dsn)
    await drain(pool, {})  # earlier tests leave events behind
    yield pool
    await pool.close()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


async def until(check: Callable[[], Awaitable[bool]], *, timeout: float = 30) -> None:
    async with asyncio.timeout(timeout):
        while not await check():
            await asyncio.sleep(0.05)


async def test_the_publisher_keeps_delivering_after_a_handler_fails(
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    proposal_id = await proposed(pool, registry)
    attempts: list[str] = []

    async def flaky(event: Event) -> None:
        attempts.append(event.payload["proposal_id"])
        if len(attempts) == 1:
            raise RuntimeError(f"consumer down: {SECRET}")

    async def delivered() -> bool:
        return len(attempts) == 2

    publishing = asyncio.create_task(
        service.publish_forever(publisher, {"proposal.created": flaky}, interval=0.01)
    )
    try:
        await until(delivered)
    finally:
        publishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await publishing

    assert attempts == [proposal_id, proposal_id]
    assert await drain(publisher, {"proposal.created": flaky}) == 0
    assert "RuntimeError" in caplog.text
    assert SECRET not in caplog.text  # a failure may quote a source; only its type is logged


async def test_the_service_runs_the_workflows_the_publisher_and_the_endpoint(
    database: Database,
    pool: asyncpg.Pool,
    publisher: asyncpg.Pool,
    registry: Registry,
    temporal_address: str,
) -> None:
    queue, port, channel = f"service-{uuid4()}", free_port(), Cards()
    connector = Quiet(f"quiet-{uuid4()}")
    serving = asyncio.create_task(
        serve(
            database.app_dsn,
            database.publisher_dsn,
            temporal_address,
            queue,
            port,
            registry=registry,
            connector=connector,
            channel=channel,
        )
    )
    try:
        temporal = await Client.connect(temporal_address)
        synced = await temporal.execute_workflow(
            SyncConnector.run,
            connector.name,
            id=sync_workflow_id(connector.name),
            task_queue=queue,
        )
        assert synced is True

        proposal_id = await proposed(pool, registry)

        async def carded() -> bool:
            return any(str(card.proposal_id) == proposal_id for card in channel.delivered)

        await until(carded)

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            assert (await http.get("/healthz")).status_code == 200
            assert (await http.post("/anything")).status_code == 204
    finally:
        serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(serving, timeout=30)


async def test_without_an_endpoint_only_the_health_check_answers() -> None:
    port = free_port()
    app = service.with_health(None)
    serving = asyncio.create_task(service.serve_http(app, port, host="127.0.0.1"))
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:

            async def healthy() -> bool:
                try:
                    return (await http.get("/healthz")).status_code == 200
                except httpx.TransportError:
                    return False

            await until(healthy)
            assert (await http.get("/anything")).status_code == 404
    finally:
        serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(serving, timeout=30)


async def test_sigterm_stops_the_service_gracefully(
    database: Database, publisher: asyncpg.Pool, temporal_address: str
) -> None:
    queue, port = f"service-{uuid4()}", free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(SERVED),
            database.app_dsn,
            database.publisher_dsn,
            temporal_address,
            queue,
            str(port),
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(SERVED.parent)])},
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:

            async def healthy() -> bool:
                try:
                    return (await http.get("/healthz")).status_code == 200
                except httpx.TransportError:
                    return False

            await until(healthy, timeout=60)
        process.send_signal(signal.SIGTERM)
        out, _ = await asyncio.to_thread(process.communicate, timeout=30)
    finally:
        process.kill()
    assert process.returncode == 0
    assert out.strip().endswith("stopped")
