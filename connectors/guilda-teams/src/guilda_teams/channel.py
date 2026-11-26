"""Teams as the kernel's channel (``guilda_kernel.channel.Channel``).

A post goes to the Teams conversation the deploy maps the kernel's thread to. A card goes
to the recipient's 1:1 chat with the bot, addressed by the Entra object id the
identities bind to the actor as ``teams:<id>``; a recipient bound to no Teams identity
gets no card, which is logged, so one misconfigured reviewer does not hold the outbox
back. The message of each card is kept by proposal and recipient in ``cards``, so
delivering the card again replaces that message. The default keeps it in memory, which
a restart forgets; the deploy passes a durable mapping (``shelve.open(path)`` is one)
until the kernel keeps the channel's reference to a delivered card.
"""

import logging
from collections.abc import MutableMapping

import httpx

from guilda_kernel.channel import Card
from guilda_kernel.identity import Identities
from guilda_teams.cards import card_activity
from guilda_teams.connector import Connector
from guilda_teams.settings import Settings

PREFIX = "teams:"
"""Channel identities of Teams: ``teams:<Entra object id>``."""

log = logging.getLogger(__name__)


class TeamsChannel:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        identities: Identities,
        *,
        cards: MutableMapping[str, tuple[str, str]] | None = None,
    ) -> None:
        self._threads = settings.threads
        self._connector = Connector(settings, http)
        self._users = {
            actor.id: channel_id.removeprefix(PREFIX)
            for actor in identities.actors
            for channel_id in reversed(actor.channel_ids)
            if channel_id.startswith(PREFIX)
        }
        self._cards = {} if cards is None else cards

    async def post(self, thread: str, text: str) -> str:
        return await self._connector.send(self._threads[thread], {"type": "message", "text": text})

    async def deliver_card(self, card: Card) -> None:
        user = self._users.get(card.recipient)
        if user is None:
            log.warning("no Teams identity for %s: card not delivered", card.recipient)
            return
        key = f"{card.proposal_id}:{card.recipient}"
        activity = card_activity(card)
        if key in self._cards:
            conversation, message = self._cards[key]
            await self._connector.update(conversation, message, activity)
            return
        conversation = await self._connector.personal_chat(user)
        self._cards[key] = (conversation, await self._connector.send(conversation, activity))
