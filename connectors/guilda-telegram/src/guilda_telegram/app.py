"""What the bot reads, by long polling ``getUpdates`` with no public endpoint: an ASGI
app the deploy serves, whose lifespan polls for as long as the service runs.

A message in the project's group (and topic, if one is set) is received in the kernel's
thread from ``telegram:<sender's user id>``; the kernel accepts it only from the internal
person bound to that identity. A button on a review card counts only from the user whose
card it is, on the digest that card showed: Approve is that person's decision; Reject asks
for the reason with a forced reply in the same private chat and decides when that person
answers it. The prompt is kept in memory, so after a restart Reject is pressed again. The
card is then edited to show the outcome. Anything else, from any other chat, is ignored.
Each update counts once it is handled, even when Telegram refuses a reply to it; any other
failure leaves it to the next poll, and the kernel keeps a redelivered message once.
Replies never say why the kernel refused.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from starlette.applications import Starlette

from guilda_kernel.channel import Card, decide
from guilda_kernel.conversation import Inbound, NoCycle, receive
from guilda_kernel.errors import GuildaError
from guilda_kernel.identity import NotAuthorized
from guilda_kernel.ontology import Registry
from guilda_kernel.state import Decision, DecisionFinal
from guilda_telegram.bot import BotApi, TelegramError
from guilda_telegram.cards import DIGEST_PREFIX, Callback, InvalidCallback, fit, read_callback
from guilda_telegram.channel import PREFIX, Cards, card_key
from guilda_telegram.settings import Settings

RECORDED: dict[Decision, str] = {
    "approved": "Aprovação registrada.",
    "rejected": "Rejeição registrada.",
}
FAILED = "Não foi possível registrar a decisão."
FINAL = "Esta proposta já foi decidida."
ASK_REASON = "Responda a esta mensagem com o motivo da rejeição."
UPDATES = ["message", "callback_query"]

log = logging.getLogger(__name__)


def create_app(
    settings: Settings,
    pool: asyncpg.Pool,
    registry: Registry,
    http: httpx.AsyncClient,
    cards: Cards,
) -> Starlette:
    """An app with no routes whose lifespan polls the bot ``settings`` configures."""
    inbox = Inbox(settings, pool, registry, http, cards)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        polling = asyncio.create_task(inbox.poll_forever())
        try:
            yield
        finally:
            polling.cancel()
            with suppress(asyncio.CancelledError):
                await polling

    return Starlette(lifespan=lifespan)


class Inbox:
    def __init__(
        self,
        settings: Settings,
        pool: asyncpg.Pool,
        registry: Registry,
        http: httpx.AsyncClient,
        cards: Cards,
    ) -> None:
        self._settings, self._pool, self._registry = settings, pool, registry
        self._bot = BotApi(settings.bot_token, http)
        self._cards = cards
        self._prompts: dict[tuple[int, int], UUID] = {}
        """Each reason prompt's message id and the user it asks to the proposal."""

    async def poll_forever(self, *, timeout: int = 25, retry: float = 5.0) -> None:
        """Handle the updates until cancelled; a long poll waits up to ``timeout`` s."""
        offset: int | None = None
        while True:
            try:
                updates = await self._bot.call(
                    "getUpdates",
                    wait=timeout + 10,
                    offset=offset,
                    timeout=timeout,
                    allowed_updates=UPDATES,
                )
                for update in updates:
                    await self.handle(update)
                    offset = update["update_id"] + 1
            except Exception as error:
                # A TelegramError's message is the method and status alone; any other
                # error is logged by its type, since its message may quote a source.
                reason = error if isinstance(error, TelegramError) else type(error).__name__
                log.warning("Telegram poll failed: %s", reason)
                await asyncio.sleep(retry)

    async def handle(self, update: dict[str, Any]) -> None:
        """Handle ``update``. A reply Telegram refuses (a press replayed after a restart is
        stale) still counts as handled, so it does not hold the next updates back."""
        try:
            if "callback_query" in update:
                await self._button(update["callback_query"])
            elif "message" in update:
                await self._message(update["message"])
        except TelegramError as error:
            log.warning("update %s: %s", update.get("update_id"), error)

    async def _message(self, message: dict[str, Any]) -> None:
        sender = message.get("from") or {}
        user, text = sender.get("id"), str(message.get("text") or "").strip()
        if sender.get("is_bot") or user is None or not text:
            return
        chat = (message.get("chat") or {}).get("id")
        if chat == self._settings.chat_id:
            await self._said(message, user, text)
        elif chat == user:
            await self._reason(message, user, text)

    async def _said(self, message: dict[str, Any], user: int, text: str) -> None:
        topic = self._settings.topic_id
        if topic is not None and message.get("message_thread_id") != topic:
            return
        thread, sender = self._settings.thread, f"{PREFIX}{user}"
        inbound = Inbound(thread, str(message["message_id"]), sender, text)
        try:
            await receive(self._pool, inbound)
        except (NotAuthorized, NoCycle) as error:
            log.info("message %s not received: %s", inbound.channel_message, type(error).__name__)

    async def _reason(self, message: dict[str, Any], user: int, text: str) -> None:
        prompt = int((message.get("reply_to_message") or {}).get("message_id", 0))
        proposal_id = self._prompts.pop((prompt, user), None)
        if proposal_id is None:
            return
        outcome = await self._decide(proposal_id, user, "rejected", text)
        await self._bot.call("sendMessage", chat_id=user, text=outcome)

    async def _button(self, query: dict[str, Any]) -> None:
        user = int((query.get("from") or {}).get("id", 0))
        callback = self._callback(query, user)
        if callback is None:
            outcome: str | None = FAILED
        elif callback.decision == "rejected":
            prompt = await self._bot.call(
                "sendMessage",
                chat_id=user,
                text=ASK_REASON,
                reply_markup={"force_reply": True, "input_field_placeholder": "Motivo"},
            )
            self._prompts[prompt["message_id"], user] = callback.proposal_id
            outcome = None
        else:
            outcome = await self._decide(callback.proposal_id, user, "approved", None)
        await self._bot.call("answerCallbackQuery", callback_query_id=query["id"], text=outcome)

    def _callback(self, query: dict[str, Any], user: int) -> Callback | None:
        """The button's request, if it is on ``user``'s card as that card shows it now."""
        try:
            callback = read_callback(query.get("data"))
        except InvalidCallback:
            return None
        card = self._cards.get(card_key(callback.proposal_id, user))
        if card is None or card[1][:DIGEST_PREFIX] != callback.digest:
            return None
        return callback

    async def _decide(
        self, proposal_id: UUID, user: int, decision: Decision, reason: str | None
    ) -> str:
        message, digest, text = self._cards[card_key(proposal_id, user)]
        try:
            await decide(
                self._pool,
                self._registry,
                f"{PREFIX}{user}",
                _card(proposal_id, digest),
                decision,
                reason=reason,
            )
        except DecisionFinal:
            return FINAL
        except GuildaError as error:
            log.info("decision on a card refused: %s", type(error).__name__)
            return FAILED
        shown = RECORDED[decision] if reason is None else f"{RECORDED[decision][:-1]}: {reason}"
        try:
            await self._bot.call(
                "editMessageText",
                chat_id=user,
                message_id=message,
                text=fit(f"{text}\n\n{shown}"),
                reply_markup={"inline_keyboard": []},
            )
        except TelegramError as error:
            log.warning("card %s not updated: %s", proposal_id, error)
        return RECORDED[decision]


def _card(proposal_id: UUID, digest: str) -> Card:
    """The card as ``decide`` reads it: only its proposal and digest count."""
    return Card(
        proposal_id=proposal_id,
        recipient="",
        package="",
        action="",
        title="",
        payload={},
        quote="",
        source="",
        digest=digest,
    )
