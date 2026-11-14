"""MCP access point (PXOS-58): the same tools for people, agents and apps.

The catalog is generated from the registry: ``list_<type>s`` per canonical type,
``timeline`` per entity and one tool per action type. Every call runs in the database
session of the token's actor, so RLS decides what each caller sees. An action tool only
creates a proposal, which goes to the approver the package declares; its evidence source
is the caller (``mcp:<actor>``), never a document the kernel did not check.

A signed token is not enough: the database must accept its actor and delegation before
the caller sees the catalog, so an unknown actor, an agent without delegation and an
external actor get 401 (PXOS-62).
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from starlette.applications import Starlette

from pxos_kernel.api.auth import Caller
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection, NotAuthorized, session
from pxos_kernel.ontology import RegisteredPackage, Registry
from pxos_kernel.state import Evidence, Fact, current, history, propose

Run = Callable[[Connection, Caller, dict[str, Any]], Awaitable[dict[str, Any]]]


class InvalidCall(PxosError):
    """The tool arguments are not what the tool accepts."""


@dataclass(frozen=True)
class Entry:
    tool: types.Tool
    run: Run


def build_server(pool: asyncpg.Pool, registry: Registry, *, name: str = "pxos") -> Server[Any]:
    catalog = _catalog(registry)

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[entry.tool for entry in catalog.values()])

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        access = get_access_token()
        entry = catalog.get(params.name)
        if access is None or entry is None:
            return _error("not authorized" if access is None else f"unknown tool {params.name}")
        caller = Caller.from_token(access)
        try:
            async with session(pool, caller.actor, caller.on_behalf_of) as connection:
                result = await entry.run(connection, caller, dict(params.arguments or {}))
        except PxosError as error:
            return _error(str(error))
        text = json.dumps(result, default=_json, ensure_ascii=False)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=json.loads(text),
        )

    return Server(name, on_list_tools=list_tools, on_call_tool=call_tool)


class AcceptedActors:
    """Token verifier that also asks the database whether it accepts the token's actor."""

    def __init__(self, verifier: TokenVerifier, pool: asyncpg.Pool) -> None:
        self._verifier = verifier
        self._pool = pool

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await self._verifier.verify_token(token)
        if access is None:
            return None
        caller = Caller.from_token(access)
        try:
            async with session(self._pool, caller.actor, caller.on_behalf_of):
                pass
        except NotAuthorized:
            return None
        return access


def http_app(
    server: Server[Any],
    verifier: TokenVerifier,
    *,
    pool: asyncpg.Pool,
    issuer_url: str,
    resource_url: str,
    host: str = "127.0.0.1",
) -> Starlette:
    """Streamable HTTP, stateless: each request carries its bearer token and its actor."""
    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host=host,
        auth=AuthSettings(
            issuer_url=issuer_url,  # type: ignore[arg-type]
            resource_server_url=resource_url,  # type: ignore[arg-type]
            validate_token_resource=False,  # the verifier checks the audience
        ),
        token_verifier=AcceptedActors(verifier, pool),
    )


def _catalog(registry: Registry) -> dict[str, Entry]:
    entries = [_timeline()]
    for registered in registry:
        for type_name in registered.models:
            entries.append(_list(registered, type_name))
        for action in registered.package.actions:
            entries.append(_action(registered, action.name))
    catalog: dict[str, Entry] = {}
    for entry in entries:
        if entry.tool.name in catalog:
            raise PxosError(f"two tools named {entry.tool.name}")
        catalog[entry.tool.name] = entry
    return catalog


def _list(registered: RegisteredPackage, type_name: str) -> Entry:
    package = registered.package
    title = package.schema.get_class(type_name).title or type_name

    async def run(connection: Connection, caller: Caller, arguments: dict[str, Any]) -> Any:
        _only(arguments, {"valid_at"})
        now = datetime.now(UTC)
        valid_at = _datetime(arguments["valid_at"]) if "valid_at" in arguments else now
        facts = await current(connection, package.name, type_name, valid_at=valid_at, known_at=now)
        return {"items": [_fact(fact) for fact in facts]}

    return Entry(
        types.Tool(
            name=f"list_{_snake(type_name)}s",
            title=f"{title} (vigentes)",
            description=(
                f"Lista os fatos de {title} vigentes na data informada (agora, se omitida), "
                "com a evidência de cada um. Mostra só o que quem chama pode ler."
            ),
            input_schema={
                "type": "object",
                "properties": {"valid_at": {"type": "string", "format": "date-time"}},
                "additionalProperties": False,
            },
        ),
        run,
    )


def _timeline() -> Entry:
    async def run(connection: Connection, caller: Caller, arguments: dict[str, Any]) -> Any:
        _only(arguments, {"entity_id"})
        try:
            entity_id = UUID(str(arguments["entity_id"]))
        except (KeyError, ValueError):
            raise InvalidCall("entity_id must be a UUID") from None
        return {"items": [_fact(fact) for fact in await history(connection, entity_id)]}

    return Entry(
        types.Tool(
            name="timeline",
            title="Linha do tempo",
            description="Todas as revisões de uma entidade, com evidência e datas.",
            input_schema={
                "type": "object",
                "properties": {"entity_id": {"type": "string", "format": "uuid"}},
                "required": ["entity_id"],
                "additionalProperties": False,
            },
        ),
        run,
    )


def _action(registered: RegisteredPackage, action_name: str) -> Entry:
    action = registered.package.action(action_name)
    title = registered.package.schema.get_class(action.creates).title or action.creates
    payload_schema = registered.models[action.creates].model_json_schema()
    definitions = payload_schema.pop("$defs", {})

    async def run(connection: Connection, caller: Caller, arguments: dict[str, Any]) -> Any:
        _only(arguments, {"payload", "quote", "valid_from", "entity_id", "base_revision"})
        payload = arguments.get("payload")
        if not isinstance(payload, dict):
            raise InvalidCall("payload must be an object")
        valid_from = (
            _datetime(arguments["valid_from"]) if "valid_from" in arguments else datetime.now(UTC)
        )
        proposal = await propose(
            connection,
            registered,
            action_name,
            payload=payload,
            evidence=Evidence(
                source=f"mcp:{caller.actor}",
                quote=str(arguments.get("quote", "")),
                context_ref=f"mcp:call/{uuid4()}",
            ),
            valid_from=valid_from,
            entity_id=_entity(arguments["entity_id"]) if "entity_id" in arguments else None,
            base_revision=_revision(arguments.get("base_revision", 0)),
        )
        return {
            "proposal_id": proposal.id,
            "entity_id": proposal.entity_id,
            "digest": proposal.digest,
            "status": "pending",
            "approver_role": action.approver,
        }

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "payload": payload_schema,
            "quote": {
                "type": "string",
                "description": "O que sustenta a proposta, nas palavras de quem chama.",
            },
            "valid_from": {"type": "string", "format": "date-time"},
            "entity_id": {
                "type": "string",
                "format": "uuid",
                "description": "A entidade que a proposta muda; omita para criar uma nova.",
            },
            "base_revision": {
                "type": "integer",
                "minimum": 1,
                "description": "A revisão sobre a qual a mudança foi feita.",
            },
        },
        "required": ["payload", "quote"],
        "additionalProperties": False,
    }
    if definitions:
        schema["$defs"] = definitions
    return Entry(
        types.Tool(
            name=action.name,
            title=f"Propor {title}",
            description=(
                f"Propõe {title}. Não grava o fato: a proposta segue para aprovação de "
                f"{action.approver}. A evidência registra quem chamou e o trecho informado."
            ),
            input_schema=schema,
        ),
        run,
    )


def _fact(fact: Fact) -> dict[str, Any]:
    return {
        "entity_id": fact.entity_id,
        "revision": fact.revision,
        "operation": fact.operation,
        "payload": fact.payload,
        "evidence": fact.evidence,
        "valid_from": fact.valid_from,
        "valid_to": fact.valid_to,
        "known_at": fact.known_at,
        "proposal_id": fact.proposal_id,
    }


def _only(arguments: dict[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(arguments) - allowed)
    if unexpected:
        raise InvalidCall(f"unexpected arguments {unexpected}")


def _entity(value: Any) -> UUID:
    try:
        return UUID(str(value))
    except ValueError:
        raise InvalidCall("entity_id must be a UUID") from None


def _revision(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidCall("base_revision must be a positive integer")
    return value


def _datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise InvalidCall(f"not an ISO date-time: {value}") from None
    if parsed.tzinfo is None:
        raise InvalidCall(f"date-time needs a timezone: {value}")
    return parsed


def _json(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )
