"""The bot's messaging endpoint, an ASGI app the deploy mounts (``POST /api/messages``).

Every call must carry the Bot Connector's token for this bot and activity
(``pxos_teams.auth``) and come from the configured tenant; otherwise 401, before anything
is read from the activity. A message in a Teams conversation mapped to a kernel thread is
received in that thread from ``teams:<sender's Entra object id>``, without the bot's
@mention; the kernel accepts it only from the internal person bound to that identity. A
click on a review card (``adaptiveCard/action``) is the clicker's decision on the
proposal and digest the card showed. Anything else is acknowledged and ignored. The
answer never carries the message, the exception or why the kernel refused: a click gets
only whether its decision was recorded.
"""

import json
import logging
import re
from typing import Any

import asyncpg
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from pxos_kernel.channel import Card, decide
from pxos_kernel.conversation import Inbound, NoCycle, receive
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import NotAuthorized
from pxos_kernel.ontology import Registry
from pxos_kernel.state import DecisionFinal
from pxos_teams.auth import Authenticator, Unauthorized
from pxos_teams.cards import Click, InvalidClick, MissingReason, read_click
from pxos_teams.channel import PREFIX
from pxos_teams.settings import Settings

MENTION = re.compile(r"<at>.*?</at>")
REPLY_CHAIN = ";messageid="
RECORDED = {"approved": "Aprovação registrada.", "rejected": "Rejeição registrada."}
FAILED = "Não foi possível registrar a decisão."

log = logging.getLogger(__name__)


def create_app(
    settings: Settings, pool: asyncpg.Pool, registry: Registry, http: httpx.AsyncClient
) -> Starlette:
    """The messaging endpoint of the bot ``settings`` configures."""
    authenticator = Authenticator(http, settings.app_id)
    threads = {conversation: thread for thread, conversation in settings.threads.items()}

    async def messages(request: Request) -> Response:
        try:
            activity = json.loads(await request.body())
            await authenticator.verify(request.headers.get("authorization"), activity)
        except (Unauthorized, ValueError, AttributeError):
            return Response(status_code=401)
        if _tenant(activity) != settings.tenant_id:
            return Response(status_code=401)
        if activity.get("type") == "invoke" and activity.get("name") == "adaptiveCard/action":
            return JSONResponse(await _click(pool, registry, activity))
        if activity.get("type") == "message":
            await _message(pool, threads, activity)
        return Response(status_code=200)

    return Starlette(routes=[Route("/api/messages", messages, methods=["POST"])])


def _tenant(activity: dict[str, Any]) -> Any:
    channel_data = activity.get("channelData") or {}
    return (channel_data.get("tenant") or {}).get("id")


async def _message(pool: asyncpg.Pool, threads: dict[str, str], activity: dict[str, Any]) -> None:
    conversation = str((activity.get("conversation") or {}).get("id", ""))
    thread = threads.get(conversation) or threads.get(conversation.split(REPLY_CHAIN)[0])
    sender = (activity.get("from") or {}).get("aadObjectId")
    text = MENTION.sub("", str(activity.get("text") or "")).strip()
    if thread is None or not sender or not text:
        return
    inbound = Inbound(thread, str(activity["id"]), f"{PREFIX}{sender}", text)
    try:
        await receive(pool, inbound)
    except (NotAuthorized, NoCycle) as error:
        log.info("message %s not received: %s", inbound.channel_message, type(error).__name__)


async def _click(pool: asyncpg.Pool, registry: Registry, activity: dict[str, Any]) -> Any:
    clicker = (activity.get("from") or {}).get("aadObjectId")
    try:
        click = read_click(activity.get("value"))
        await decide(
            pool, registry, f"{PREFIX}{clicker}", _card(click), click.decision, reason=click.reason
        )
    except MissingReason:
        return _answer("Informe o motivo da rejeição.")
    except InvalidClick:
        return _answer(FAILED)
    except DecisionFinal:
        return _answer("Esta proposta já foi decidida.")
    except PxosError as error:
        log.info("click on a card refused: %s", type(error).__name__)
        return _answer(FAILED)
    return _answer(RECORDED[click.decision])


def _card(click: Click) -> Card:
    """The card as ``decide`` reads it: only its proposal and digest count."""
    return Card(
        proposal_id=click.proposal_id,
        recipient="",
        package="",
        action="",
        title="",
        payload={},
        quote="",
        source="",
        digest=click.digest,
    )


def _answer(text: str) -> dict[str, Any]:
    return {"statusCode": 200, "type": "application/vnd.microsoft.activity.message", "value": text}
