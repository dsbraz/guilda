"""BRQ@work runs as one command that reads the deploy's environment, bootstraps the
installation and hands it to the kernel's service until SIGTERM.

    python -m brq_at_work.serve
"""

import asyncio
import runpy
import socket
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from db_testing import Database
from model_testing import ROUTE
from pxos_kernel import service
from pxos_kernel.model_access import Route

from .test_adapters import REQUIRED, SECRETS

ROUTE_ENVIRONMENT = {
    "PXOS_MODEL_PROVIDER": ROUTE.provider,
    "PXOS_MODEL": ROUTE.model,
    "PXOS_MODEL_REGION": ROUTE.region,
    "PXOS_MEMORY_MODEL": ROUTE.memory_model,
    "PXOS_MEMORY_REGION": ROUTE.memory_region,
    "PXOS_EMBEDDING_MODEL": ROUTE.embedding_model,
    "PXOS_EMBEDDING_REGION": ROUTE.embedding_region,
    "PXOS_MODEL_INPUT_PRICE": str(ROUTE.input_price),
    "PXOS_MODEL_OUTPUT_PRICE": str(ROUTE.output_price),
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


async def polled(temporal: Client, queue: str) -> bool:
    described = await temporal.workflow_service.describe_task_queue(
        DescribeTaskQueueRequest(
            namespace="default",
            task_queue=TaskQueue(name=queue),
            task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
        )
    )
    return bool(described.pollers)


async def healthy(port: int) -> bool:
    try:
        async with httpx.AsyncClient() as http:
            return (await http.get(f"http://127.0.0.1:{port}/healthz")).status_code == 200
    except httpx.TransportError:
        return False


async def probe(main: Coroutine[Any, Any, None], address: str, queue: str, port: int) -> None:
    """Run ``main`` until its worker polls ``queue`` and it answers the health check."""
    serving = asyncio.ensure_future(main)
    temporal = await Client.connect(address)
    try:
        async with asyncio.timeout(60):
            while not (await polled(temporal, queue) and await healthy(port)):
                assert not serving.done(), serving.exception()
                await asyncio.sleep(0.1)
    finally:
        serving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await serving


def test_the_command_bootstraps_and_serves_the_installation(
    database: Database,
    temporal_address: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue, port = f"brq-{uuid4()}", free_port()
    environment = {
        **ROUTE_ENVIRONMENT,
        "PXOS_OWNER_DATABASE_URL": database.owner_dsn,
        "PXOS_DATABASE_URL": database.app_dsn,
        "PXOS_PUBLISHER_DATABASE_URL": database.publisher_dsn,
        "PXOS_TEMPORAL_ADDRESS": temporal_address,
        "PXOS_TASK_QUEUE": queue,
        "PXOS_HTTP_PORT": str(port),
        "PXOS_VERTEX_PROJECT": "project-a",
        "PXOS_HINDSIGHT_URL": "http://127.0.0.1:9",
        **REQUIRED,
        **SECRETS,
        "PXOS_TEAMS_CARDS_PATH": str(tmp_path / "cards"),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    served: list[None] = []

    def run(main: Coroutine[Any, Any, None]) -> None:
        served.append(asyncio.run(probe(main, temporal_address, queue, port)))

    monkeypatch.setattr(service, "run", run)
    monkeypatch.setattr(sys, "argv", ["serve"])
    monkeypatch.delitem(sys.modules, "brq_at_work.serve", raising=False)
    runpy.run_module("brq_at_work.serve", run_name="__main__")

    assert served == [None]
    assert asyncio.run(installed_route(database)) == ROUTE


async def installed_route(database: Database) -> Route:
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        row = await owner.fetchrow(
            "SELECT provider, model, region, memory_model, memory_region, embedding_model,"
            " embedding_region, input_price, output_price FROM pxos.installation"
        )
    finally:
        await owner.close()
    assert row is not None
    return Route(**dict(row))
