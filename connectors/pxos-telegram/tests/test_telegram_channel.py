"""The bot speaking first: posts in the project's group and review cards in each reviewer's
private chat, through the Bot API.

Posts go to the group (and topic) the deploy maps the kernel's thread to. A card goes to
the private chat of the Telegram user bound to the actor as ``telegram:<id>``, with
Approve and Reject buttons; delivering it again edits that same message. The bot's token
travels in the Bot API's URLs, so it never reaches a log or an error.
"""

import logging
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from telegram_testing import CHAT, SETTINGS, THREAD, TOKEN, TOPIC, USERS, Telegram

from pxos_kernel.channel import Card
from pxos_kernel.identity import Actor, Identities
from pxos_telegram import TelegramChannel, TelegramError
from pxos_telegram.cards import LIMIT, read_callback

IDENTITIES = Identities(
    actors=(
        Actor(
            id="carla",
            kind="internal_person",
            name="Carla",
            channel_ids=("teams:aad-carla", f"telegram:{USERS['carla']}"),
        ),
        Actor(id="gina", kind="internal_person", name="Gina", channel_ids=("teams:gina",)),
    )
)


def channel(telegram: Telegram, **settings: Any) -> TelegramChannel:
    return TelegramChannel(replace(SETTINGS, **settings), telegram.client(), IDENTITIES)


def card(recipient: str = "carla", **overrides: Any) -> Card:
    base: dict[str, Any] = {
        "proposal_id": uuid4(),
        "recipient": recipient,
        "package": "brq_weekly_report",
        "action": "propose_weekly_report",
        "title": "Report semanal",
        "payload": {
            "summary": "Semana estável.",
            "items": [{"category": "progress", "text": "API entregue.", "sources": ["m:1", "d:2"]}],
        },
        "quote": "A API foi entregue na quarta.",
        "source": "agent-run:42",
        "digest": "d" * 64,
    }
    base.update(overrides)
    return Card(**base)


async def test_a_post_goes_to_the_projects_group_as_plain_text() -> None:
    telegram = Telegram()

    sent = await channel(telegram).post(THREAD, "Quantas caixas chegam?")

    assert sent == "101"
    assert telegram.sent("sendMessage") == [{"chat_id": CHAT, "text": "Quantas caixas chegam?"}]


async def test_a_post_goes_to_the_configured_topic() -> None:
    telegram = Telegram()

    await channel(telegram, topic_id=TOPIC).post(THREAD, "Oi")

    [sent] = telegram.sent("sendMessage")
    assert sent["message_thread_id"] == TOPIC


async def test_a_post_to_an_unknown_thread_fails() -> None:
    with pytest.raises(KeyError):
        await channel(Telegram()).post("other-thread", "Oi")


async def test_a_long_text_is_cut_to_what_telegram_takes() -> None:
    telegram = Telegram()

    await channel(telegram).post(THREAD, "x" * (LIMIT + 10))

    [sent] = telegram.sent("sendMessage")
    assert len(sent["text"]) == LIMIT and sent["text"].endswith("…")


async def test_a_card_goes_to_the_reviewers_private_chat_with_its_buttons() -> None:
    telegram, review = Telegram(), card()

    await channel(telegram).deliver_card(review)

    [sent] = telegram.sent("sendMessage")
    assert sent["chat_id"] == USERS["carla"]
    text = sent["text"]
    assert text.startswith("Report semanal\n")
    assert "summary:\nSemana estável." in text
    assert "items:\n- category: progress · text: API entregue. · sources: m:1, d:2" in text
    assert "“A API foi entregue na quarta.”\nFonte: agent-run:42" in text
    [[approve, reject]] = sent["reply_markup"]["inline_keyboard"]
    assert (approve["text"], reject["text"]) == ("Aprovar", "Rejeitar")
    for button, decision in [(approve, "approved"), (reject, "rejected")]:
        assert len(button["callback_data"].encode()) <= 64
        callback = read_callback(button["callback_data"])
        assert (callback.decision, callback.proposal_id) == (decision, review.proposal_id)
        assert review.digest.startswith(callback.digest)


async def test_a_card_delivered_again_edits_its_message() -> None:
    telegram, cards = Telegram(), dict[str, tuple[int, str, str]]()
    review = card()
    bot = TelegramChannel(SETTINGS, telegram.client(), IDENTITIES, cards=cards)

    await bot.deliver_card(review)
    await bot.deliver_card(replace(review, title="Report semanal (revisto)"))
    await bot.deliver_card(replace(review, title="Report semanal (revisto)"))

    [edited] = telegram.sent("editMessageText")
    assert (edited["chat_id"], edited["message_id"]) == (USERS["carla"], 101)
    assert edited["text"].startswith("Report semanal (revisto)")
    assert edited["reply_markup"]["inline_keyboard"]
    assert len(telegram.sent("sendMessage")) == 1
    assert list(cards) == [f"{review.proposal_id}:{USERS['carla']}"]


async def test_a_reviewer_without_telegram_gets_no_card_and_it_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    telegram = Telegram()

    with caplog.at_level(logging.WARNING):
        await channel(telegram).deliver_card(card(recipient="gina"))

    assert telegram.calls == []
    assert "gina" in caplog.text


async def test_a_reviewer_who_never_started_the_bot_gets_no_card_and_it_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram refuses (403) a private chat the person never opened; raising would hold
    the whole outbox back until they did."""
    telegram = Telegram()
    telegram.fail = 403

    with caplog.at_level(logging.WARNING):
        await channel(telegram).deliver_card(card())

    assert "carla" in caplog.text and "/start" in caplog.text


@pytest.mark.parametrize("status", [400, 429, 500])
async def test_any_other_refusal_of_a_card_raises_for_the_outbox_to_retry(status: int) -> None:
    telegram = Telegram()
    telegram.fail = status

    with pytest.raises(TelegramError):
        await channel(telegram).deliver_card(card())


@pytest.mark.parametrize("status", [400, 403, 500])
async def test_a_refused_call_raises_without_the_token(status: int) -> None:
    telegram = Telegram()
    telegram.fail = status

    with pytest.raises(TelegramError) as raised:
        await channel(telegram).post(THREAD, "Oi")

    assert str(status) in str(raised.value)
    assert TOKEN not in str(raised.value) and TOKEN not in repr(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


async def test_a_network_failure_raises_without_the_token() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    http = httpx.AsyncClient(transport=httpx.MockTransport(unreachable))
    bot = TelegramChannel(SETTINGS, http, IDENTITIES)

    with pytest.raises(TelegramError, match="ConnectError") as raised:
        await bot.post(THREAD, "Oi")

    assert TOKEN not in str(raised.value) and raised.value.__cause__ is None
    assert raised.value.__suppress_context__


async def test_an_answer_that_is_not_the_bot_apis_raises() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    with pytest.raises(TelegramError, match="200"):
        await TelegramChannel(SETTINGS, http, IDENTITIES).post(THREAD, "Oi")


async def test_the_token_never_reaches_the_log(caplog: pytest.LogCaptureFixture) -> None:
    telegram = Telegram()

    with caplog.at_level(logging.DEBUG):
        await channel(telegram).post(THREAD, "Oi")

    assert "api.telegram.org/bot<token>/sendMessage" in caplog.text
    assert TOKEN not in caplog.text


def test_the_settings_do_not_show_the_token() -> None:
    assert TOKEN not in repr(SETTINGS)
