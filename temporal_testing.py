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
