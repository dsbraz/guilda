"""Telegram as the kernel's channel (``pxos_kernel.channel.Channel``).

A post goes to the group (and topic) the deploy maps the kernel's thread to. A card goes
to the private chat of the Telegram user the identities bind to the recipient as
``telegram:<user id>``, which Telegram opens only after that person sends the bot
/start. A recipient bound to no Telegram user, or who never sent /start (Telegram
answers 403), gets no card, which is logged, so one misconfigured reviewer does not hold
the outbox back; any other failure raises, for the outbox to try again. Each card's
message is kept in ``cards`` under the proposal and the user, with the digest and the
text it shows, so delivering the card again edits that message and the inbox decides on
that digest. The default keeps it in memory, which a restart forgets; the deploy passes
a durable mapping (``shelve.open(path)`` is one).
"""

import logging
from collections.abc import MutableMapping

import httpx

from pxos_kernel.channel import Card
from pxos_kernel.identity import Identities
from pxos_telegram.bot import BotApi, TelegramError
from pxos_telegram.cards import card_text, fit, keyboard
from pxos_telegram.settings import Settings

FORBIDDEN = 403
PREFIX = "telegram:"
"""Channel identities of Telegram: ``telegram:<user id>``."""

Cards = MutableMapping[str, tuple[int, str, str]]
"""``<proposal id>:<user id>`` to the card's message id, digest and text."""

log = logging.getLogger(__name__)


def card_key(proposal_id: object, user: object) -> str:
    return f"{proposal_id}:{user}"


class TelegramChannel:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        identities: Identities,
        *,
        cards: Cards | None = None,
    ) -> None:
        self._settings = settings
        self._bot = BotApi(settings.bot_token, http)
        self._users = {
            actor.id: int(channel_id.removeprefix(PREFIX))
            for actor in identities.actors
            for channel_id in reversed(actor.channel_ids)
            if channel_id.startswith(PREFIX)
        }
        self._cards: Cards = {} if cards is None else cards

    async def post(self, thread: str, text: str) -> str:
        if thread != self._settings.thread:
            raise KeyError(thread)
        sent = await self._bot.call(
            "sendMessage",
            chat_id=self._settings.chat_id,
            message_thread_id=self._settings.topic_id,
            text=fit(text),
        )
        return str(sent["message_id"])

    async def deliver_card(self, card: Card) -> None:
        user = self._users.get(card.recipient)
        if user is None:
            log.warning("no Telegram identity for %s: card not delivered", card.recipient)
            return
        key, text, buttons = card_key(card.proposal_id, user), card_text(card), keyboard(card)
        if key in self._cards:
            message, _, shown = self._cards[key]
            if text != shown:  # Telegram refuses an edit that changes nothing
                await self._bot.call(
                    "editMessageText",
                    chat_id=user,
                    message_id=message,
                    text=text,
                    reply_markup=buttons,
                )
                self._cards[key] = (message, card.digest, text)
            return
        try:
            sent = await self._bot.call(
                "sendMessage", chat_id=user, text=text, reply_markup=buttons
            )
        except TelegramError as error:
            if error.status != FORBIDDEN:
                raise
            log.warning("%s has not sent /start to the bot: card not delivered", card.recipient)
            return
        self._cards[key] = (sent["message_id"], card.digest, text)
