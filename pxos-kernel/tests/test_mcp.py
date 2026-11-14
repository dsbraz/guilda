"""MCP access point (PXOS-58): one catalog generated from the registry, served over HTTP.

Every call runs in the database session of the token's actor, so RLS decides what each
caller sees. An action tool only creates a proposal; its evidence source is set by the
kernel, never by the caller.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette

from mcp_testing import BASE, ToolError, call, connect, post_status, running
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import _catalog, build_server, http_app
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import session
from pxos_kernel.ontology import RegisteredPackage, Registry
from pxos_kernel.state import get_proposal, review

ISSUER = "http://idp.test"
AUDIENCE = "pxos-test"
ORDER = {"sku": "M-1", "quantity": 2, "occurred_at": "2026-11-14T09:00:00Z"}


@pytest.fixture(scope="module")
def issuer() -> LocalIssuer:
    return LocalIssuer.generate(issuer=ISSUER, audience=AUDIENCE)


@pytest.fixture
async def app(
    pool: asyncpg.Pool, registry: Registry, issuer: LocalIssuer
) -> AsyncIterator[Starlette]:
    verifier = JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=issuer.jwks())
    app = http_app(
        build_server(pool, registry),
        verifier,
        pool=pool,
        issuer_url=ISSUER,
        resource_url=f"{BASE}/mcp",
    )
    async with running(app):
        yield app


async def test_a_call_without_a_valid_token_is_refused(app: Starlette) -> None:
    foreign = LocalIssuer.generate(issuer=ISSUER, audience=AUDIENCE).token("erin")
    for token in (None, "not-a-token", foreign):
        assert await post_status(app, token) == 401


async def test_catalog_is_generated_from_the_registry(app: Starlette, issuer: LocalIssuer) -> None:
    async with connect(app, issuer.token("erin")) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {
        "list_restock_orders",
        "timeline",
        "create_restock_order",
        "list_agent_versions",
        "publish_agent_version",
    }
    schema = tools["create_restock_order"].input_schema
    assert set(schema["properties"]) == {
        "payload",
        "quote",
        "valid_from",
        "entity_id",
        "base_revision",
    }
    assert {"payload", "quote"} <= set(schema["required"])
    assert schema["additionalProperties"] is False
    assert {"sku", "quantity"} <= set(schema["properties"]["payload"]["properties"])


async def test_an_action_tool_creates_a_proposal_with_the_callers_statement(
    app: Starlette, pool: asyncpg.Pool, issuer: LocalIssuer
) -> None:
    created = await call(
        app,
        issuer.token("erin"),
        "create_restock_order",
        {"payload": ORDER, "quote": "Faltam 2 M-1."},
    )
    assert created["status"] == "pending"
    assert created["approver_role"] == "store-manager"
    async with session(pool, "erin") as connection:
        proposal = await get_proposal(connection, created["proposal_id"])
    assert proposal.author == "erin"
    assert proposal.evidence["source"] == "mcp:erin"
    assert proposal.evidence["quote"] == "Faltam 2 M-1."
    assert proposal.digest == created["digest"]


async def test_an_agent_version_is_proposed_like_any_fact(
    app: Starlette, issuer: LocalIssuer
) -> None:
    """Before the web form, a definition is published through the MCP (ADR 0004 §3)."""
    definition = {
        "agent": "restock-agent",
        "instructions": "Read each restock promise.",
        "memory_instructions": "Extract every restock promise.",
        "proposes": "retail.create_restock_order",
    }
    created = await call(
        app,
        issuer.token("frank"),
        "publish_agent_version",
        {"payload": definition, "quote": "Primeira versão."},
    )
    assert (created["status"], created["approver_role"]) == ("pending", "platform-admin")


async def test_an_action_tool_proposes_a_change_to_an_existing_entity(
    app: Starlette, pool: asyncpg.Pool, registry: Registry, issuer: LocalIssuer
) -> None:
    """A new version is a revision of the same entity, not a second one."""
    created = await call(
        app, issuer.token("erin"), "create_restock_order", {"payload": ORDER, "quote": "Faltam 2."}
    )
    async with session(pool, "carla") as connection:
        await review(
            connection, registry, created["proposal_id"], "approved", digest=created["digest"]
        )
    changed = await call(
        app,
        issuer.token("erin"),
        "create_restock_order",
        {
            "payload": {**ORDER, "quantity": 3},
            "quote": "Faltam 3.",
            "entity_id": str(created["entity_id"]),
            "base_revision": 1,
        },
    )
    assert changed["entity_id"] == created["entity_id"]
    async with session(pool, "erin") as connection:
        assert (await get_proposal(connection, changed["proposal_id"])).base_revision == 1


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"payload": ORDER, "quote": "q", "source": "fixture:transcript"}, "unexpected"),
        ({"payload": ORDER, "quote": " "}, "evidence quote"),
        ({"payload": {**ORDER, "quantity": 0}, "quote": "q"}, "invalid RestockOrder"),
        ({"payload": {**ORDER, "assignee": "nobody"}, "quote": "q"}, "unknown actor nobody"),
        ({"payload": ORDER, "quote": "q", "valid_from": "2026-11-14"}, "timezone"),
        ({"payload": ORDER, "quote": "q", "valid_from": "amanhã"}, "not an ISO date-time"),
        ({"payload": "M-1", "quote": "q"}, "payload must be an object"),
        ({"payload": ORDER, "quote": "q", "entity_id": "x"}, "entity_id must be a UUID"),
        ({"payload": ORDER, "quote": "q", "base_revision": -1}, "positive integer"),
        ({"payload": ORDER, "quote": "q", "base_revision": True}, "positive integer"),
    ],
)
async def test_an_invalid_action_call_creates_nothing(
    app: Starlette, issuer: LocalIssuer, arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ToolError, match=message):
        await call(app, issuer.token("erin"), "create_restock_order", arguments)


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(("nobody", None), id="unknown-actor"),
        pytest.param(("gina", "restock-agent"), id="agent-without-delegation"),
        pytest.param(("vera", None), id="external-actor"),
    ],
)
async def test_a_token_whose_actor_the_database_refuses_gets_no_tools(
    app: Starlette, issuer: LocalIssuer, token: tuple[str, str | None]
) -> None:
    """A valid signature is not enough (PXOS-62): the database must accept the actor before
    the caller sees the catalog or calls a tool."""
    person, agent = token
    assert await post_status(app, issuer.token(person, agent=agent)) == 401


async def test_list_and_timeline_show_the_approved_fact(
    app: Starlette, pool: asyncpg.Pool, registry: Registry, issuer: LocalIssuer
) -> None:
    created = await call(
        app, issuer.token("erin"), "create_restock_order", {"payload": ORDER, "quote": "Faltam 2."}
    )
    async with session(pool, "carla") as connection:
        await review(
            connection, registry, created["proposal_id"], "approved", digest=created["digest"]
        )

    listed = await call(app, issuer.token("erin"), "list_restock_orders", {})
    [fact] = [item for item in listed["items"] if item["proposal_id"] == created["proposal_id"]]
    assert fact["payload"]["sku"] == "M-1"

    timeline = await call(app, issuer.token("erin"), "timeline", {"entity_id": fact["entity_id"]})
    assert [(r["revision"], r["evidence"]["source"]) for r in timeline["items"]] == [
        (1, "mcp:erin")
    ]


async def test_a_malformed_timeline_call_or_an_unknown_tool_is_an_error(
    app: Starlette, issuer: LocalIssuer
) -> None:
    with pytest.raises(ToolError, match="entity_id must be a UUID"):
        await call(app, issuer.token("erin"), "timeline", {"entity_id": "x"})
    with pytest.raises(ToolError, match="unknown tool restock"):
        await call(app, issuer.token("erin"), "restock", {})


class Line(BaseModel):
    sku: str


class Order(BaseModel):
    lines: list[Line]


def test_payload_definitions_resolve_from_the_tool_schema(registry: Registry) -> None:
    """A payload with nested types keeps its $defs at the root, where its $refs point."""
    retail = registry.get("retail")
    nested = RegisteredPackage(retail.package, {**retail.models, "RestockOrder": Order})
    [tool] = [e.tool for e in _catalog([nested]).values() if e.tool.name == "create_restock_order"]  # type: ignore[arg-type]
    payload = tool.input_schema["properties"]["payload"]
    assert payload["properties"]["lines"]["items"] == {"$ref": "#/$defs/Line"}
    assert set(tool.input_schema["$defs"]) == {"Line"}


def test_two_tools_with_the_same_name_are_refused(registry: Registry) -> None:
    retail = registry.get("retail")
    with pytest.raises(PxosError, match="two tools named"):
        _catalog([retail, retail])  # type: ignore[arg-type]
