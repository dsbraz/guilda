"""Shared MCP test helpers: talk to the kernel's MCP app over HTTP, in process.

Requests go through the real Starlette app (bearer middleware, streamable HTTP) on an
ASGI transport, so tests see what a client on the network would.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

BASE = "http://127.0.0.1:8000"


@asynccontextmanager
async def running(app: Starlette) -> AsyncIterator[Starlette]:
    """Run the app's lifespan (the MCP session manager) around the test.

    The lifespan holds a task group, which must exit in the task that entered it; pytest
    may tear a fixture down in another task, so a dedicated task holds it.
    """
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await started.wait()
    try:
        yield app
    finally:
        stop.set()
        await task


@asynccontextmanager
async def connect(app: Starlette, token: str | None) -> AsyncIterator[Client]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx2.ASGITransport(app=app)
    async with (
        httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers) as http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as client,
    ):
        yield client


async def post_status(app: Starlette, token: str | None) -> int:
    """HTTP status of a bare MCP request; the bearer middleware answers before MCP does."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url=BASE, headers=headers) as http:
        response = await http.post("/mcp", json={})
    return response.status_code


async def call(app: Starlette, token: str, tool: str, arguments: dict[str, Any]) -> Any:
    """Call ``tool`` and return its JSON result; a tool error raises ToolError."""
    async with connect(app, token) as client:
        result = await client.call_tool(tool, arguments)
    text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
    if result.is_error:
        raise ToolError(text)
    return json.loads(text)


class ToolError(Exception):
    """The tool answered with is_error."""
