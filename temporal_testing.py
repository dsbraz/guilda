"""Shared Temporal test helpers: one development server per test session.

The server is the Temporal CLI dev server that the SDK downloads (pinned version). It runs
under its own event loop in a background thread, because pytest gives each test a fresh
loop; tests connect their own client to its address.
"""

import asyncio
import threading
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

DEV_SERVER_VERSION = "v1.10.1"


@contextmanager
def dev_server() -> Iterator[str]:
    """Start the dev server, yield its address (host:port) and stop it afterwards."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout=120)

    environment = run(
        WorkflowEnvironment.start_local(
            dev_server_download_version=DEV_SERVER_VERSION,
            dev_server_startup_timeout=timedelta(seconds=60),
        )
    )
    try:
        yield environment.client.service_client.config.target_host
    finally:
        run(environment.shutdown())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)


async def history_bytes(client: Client, workflow_id: str) -> bytes:
    """A workflow's history and its children's, as raw protobuf: payloads, failure messages
    and stack traces all appear there as bytes, so content can be searched for directly."""
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    raw = b"".join(event.SerializeToString() for event in history.events)
    for event in history.events:
        if event.HasField("start_child_workflow_execution_initiated_event_attributes"):
            child = event.start_child_workflow_execution_initiated_event_attributes.workflow_id
            raw += await history_bytes(client, child)
    return raw
