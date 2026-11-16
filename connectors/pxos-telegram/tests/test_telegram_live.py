"""The adapter against the real Bot API; opt-in.

Set PXOS_TELEGRAM_BOT_TOKEN, PXOS_TELEGRAM_CHAT_ID (a group the bot is in), optionally
PXOS_TELEGRAM_TOPIC_ID, and PXOS_TELEGRAM_USER (the user id of someone who has sent
/start to the bot). It posts a message in the group and a test card, delivered twice, to
the user. It never reads updates, so it does not take them from a running instance.
"""

import os
from uuid import uuid4

import httpx
import pytest

from pxos_kernel.channel import Card
from pxos_kernel.identity import Actor, Identities
from pxos_telegram import Settings, TelegramChannel

ENV = {
    name: os.environ.get(f"PXOS_TELEGRAM_{name}", "")
    for name in ["BOT_TOKEN", "CHAT_ID", "TOPIC_ID", "USER"]
}
pytestmark = pytest.mark.skipif(
    not (ENV["BOT_TOKEN"] and ENV["CHAT_ID"] and ENV["USER"]), reason="PXOS_TELEGRAM_* not set"
)


async def test_the_bot_posts_and_edits_a_card_for_real() -> None:
    settings = Settings(
        bot_token=ENV["BOT_TOKEN"],
        chat_id=int(ENV["CHAT_ID"]),
        thread="live",
        topic_id=int(ENV["TOPIC_ID"]) if ENV["TOPIC_ID"] else None,
    )
    identities = Identities(
        actors=(
            Actor(
                id="pm",
                kind="internal_person",
                name="PM",
                channel_ids=(f"telegram:{ENV['USER']}",),
            ),
        )
    )
    card = Card(
        proposal_id=uuid4(),
        recipient="pm",
        package="live",
        action="live",
        title="Teste do adaptador Telegram",
        payload={"summary": "Pode ignorar."},
        quote="—",
        source="test_telegram_live",
        digest="0" * 64,
    )
    async with httpx.AsyncClient(timeout=30) as http:
        telegram = TelegramChannel(settings, http, identities)
        assert await telegram.post("live", "Teste do adaptador Telegram do pxOS; pode ignorar.")
        await telegram.deliver_card(card)
        await telegram.deliver_card(Card(**{**card.__dict__, "title": "Teste (editado)"}))
