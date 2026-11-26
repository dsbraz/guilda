"""What the bot reads by long polling, end to end against the real database.

A message in the project's group becomes a message of the kernel's thread from
``telegram:<user id>``, accepted only from the internal person bound to it; anything from
another chat is ignored. A button on a review card is the clicker's decision on the
proposal and the digest the card showed; a rejection asks for its reason with a forced
reply, taken only from the same person. After the decision the card shows the outcome.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import retail_fixture.generated as retail_models
from telegram_testing import SETTINGS, TOPIC, USERS, Telegram, message

from db_testing import Database, create_test_database
from guilda_kernel.channel import route_cards
from guilda_kernel.conversation import Message, conversation, post
from guilda_kernel.identity import Identities, install_identities, read_identities, session
from guilda_kernel.model_access import install_route
from guilda_kernel.ontology import Registry
from guilda_kernel.service import with_health
from guilda_kernel.state import (
    Event,
    Evidence,
    InvalidProposal,
    Review,
    create_pool,
    get_review,
    install_packages,
    propose,
)
from guilda_telegram import TelegramChannel, create_app
from guilda_telegram.app import Inbox
from model_testing import ROUTE

AGENT = "restock-agent"
IDENTITIES: Identities = read_identities(Path(__file__).parent / "identities.yaml")


@pytest.fixture(scope="module")
def registry() -> Registry:
    registry = Registry()
    registry.register(Path(retail_models.__file__).parent, retail_models)
    return registry


@pytest.fixture(scope="module")
def database(registry: Registry) -> Database:
    """An installation of the kernel's retail fixture whose people are bound to Telegram."""
    database = create_test_database("pxos_test_telegram")

    async def bootstrap() -> None:
        owner = await asyncpg.connect(database.owner_dsn)
        try:
            await install_identities(owner, IDENTITIES)
            await install_packages(owner, registry)
            await install_route(owner, ROUTE)
        finally:
            await owner.close()

    asyncio.run(bootstrap())
    return database


@pytest.fixture
async def pool(database: Database) -> AsyncIterator[asyncpg.Pool]:
    pool = await create_pool(database.app_dsn)
    yield pool
    await pool.close()


class Bot:
    """The inbox and the channel of one project thread, over a faked Telegram."""

    def __init__(self, pool: asyncpg.Pool, registry: Registry, **settings: Any) -> None:
        self.pool, self.registry = pool, registry
        self.cycle = f"cycle-{uuid4()}"
        self.settings = replace(SETTINGS, thread=f"thread-{uuid4()}", **settings)
        self.telegram = Telegram()
        self.cards: dict[str, Any] = {}
        self.channel = TelegramChannel(
            self.settings, self.telegram.client(), IDENTITIES, cards=self.cards
        )
        self.inbox = Inbox(self.settings, pool, registry, self.telegram.client(), self.cards)
        self._update = 0

    async def ask(self) -> Message:
        return await post(
            self.pool,
            self.channel,
            AGENT,
            thread=self.settings.thread,
            cycle=self.cycle,
            text="Quantas?",
        )

    async def send(self, **update: Any) -> None:
        self._update += 1
        await self.inbox.handle({"update_id": self._update, **update})

    async def said(self) -> list[Message]:
        async with session(self.pool, AGENT) as connection:
            return [m for m in await conversation(connection, self.cycle) if m.sender]


@pytest.fixture
async def bot(pool: asyncpg.Pool, registry: Registry) -> Bot:
    return Bot(pool, registry)


async def test_a_message_in_the_projects_group_is_received_from_its_sender(bot: Bot) -> None:
    await bot.ask()
    sent = message()

    await bot.send(message=sent)

    [received] = await bot.said()
    assert (received.author, received.sender) == ("erin", f"telegram:{USERS['erin']}")
    assert (received.text, received.channel_message) == (
        "Cinco caixas chegam na terça.",
        str(sent["message_id"]),
    )


async def test_a_redelivered_message_is_kept_once(bot: Bot) -> None:
    await bot.ask()

    await bot.send(message=message())
    await bot.send(message=message())

    assert len(await bot.said()) == 1


async def test_with_a_topic_only_its_messages_are_received(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    bot = Bot(pool, registry, topic_id=TOPIC)
    await bot.ask()

    await bot.send(message=message(message_thread_id=TOPIC + 1))
    await bot.send(message=message(user="carla"))
    await bot.send(message=message(user="diego", message_thread_id=TOPIC))

    assert [m.author for m in await bot.said()] == ["diego"]
    assert bot.telegram.sent("sendMessage")[0]["message_thread_id"] == TOPIC


@pytest.mark.parametrize(
    "update",
    [
        {"message": message(user="vera")},
        {"message": message(user="stranger")},
        {"message": message(text="  ")},
        {"message": {k: v for k, v in message().items() if k != "text"}},
        {"message": message(chat=-100999)},
        {"message": message(chat=USERS["erin"])},
        {"edited_message": message()},
        {"message": {**message(), "from": {"id": 77, "is_bot": True}}},
    ],
    ids=["external", "unbound", "blank", "no-text", "other-group", "private", "edit", "bot"],
)
async def test_anything_else_is_ignored(bot: Bot, update: dict[str, Any]) -> None:
    await bot.ask()

    await bot.send(**update)

    assert await bot.said() == []


async def test_a_message_before_any_question_belongs_to_no_cycle(bot: Bot) -> None:
    await bot.send(message=message())

    assert await bot.said() == []


# Review cards


async def proposed(pool: asyncpg.Pool, registry: Registry) -> UUID:
    async with session(pool, "erin") as connection:
        proposal = await propose(
            connection,
            registry.get("retail"),
            "create_restock_order",
            payload={"sku": "C-3", "quantity": 5, "occurred_at": "2026-11-01T09:00:00Z"},
            evidence=Evidence(source="fixture:shelf-log", quote="restock 5", context_ref="ctx"),
            valid_from=datetime(2026, 11, 1, tzinfo=UTC),
        )
    return proposal.id


async def carded(bot: Bot) -> tuple[UUID, dict[str, Any]]:
    """A proposal routed to the reviewers; Carla's card as Telegram shows it."""
    proposal_id = await proposed(bot.pool, bot.registry)
    created = Event(
        id=1,
        event_type="proposal.created",
        entity_id=uuid4(),
        payload={
            "proposal_id": str(proposal_id),
            "package": "retail",
            "action": "create_restock_order",
        },
        created_at=datetime.now(UTC),
    )
    await route_cards(bot.pool, bot.registry, bot.channel)(created)
    [sent] = [m for m in bot.telegram.sent("sendMessage") if m["chat_id"] == USERS["carla"]]
    return proposal_id, sent


def click(card: dict[str, Any], verb: str, who: str = "carla", **data: str) -> dict[str, Any]:
    """The callback query Telegram brings when ``who`` presses ``verb`` on ``card``."""
    [button] = [b for b in card["reply_markup"]["inline_keyboard"][0] if b["text"] == verb]
    return {
        "id": f"cb-{uuid4()}",
        "from": {"id": USERS[who], "is_bot": False},
        "message": {"message_id": 101, "chat": {"id": USERS[who], "type": "private"}},
        "data": data.get("data", button["callback_data"]),
    }


async def decision(bot: Bot, proposal_id: UUID) -> Review | None:
    async with session(bot.pool, "carla") as connection:
        try:
            return await get_review(connection, proposal_id)
        except InvalidProposal:
            return None


def answered(bot: Bot) -> str:
    return str(bot.telegram.sent("answerCallbackQuery")[-1].get("text", ""))


async def test_approving_on_the_card_records_the_decision_and_shows_it(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    await bot.send(callback_query=click(card, "Aprovar"))

    review = await decision(bot, proposal_id)
    assert review is not None
    assert (review.decision, review.reviewer, review.reason) == ("approved", "carla", None)
    assert answered(bot) == "Aprovação registrada."
    [edited] = bot.telegram.sent("editMessageText")
    assert (edited["chat_id"], edited["message_id"]) == (USERS["carla"], 101)
    assert edited["text"].startswith(card["text"])
    assert edited["text"].endswith("Aprovação registrada.")
    assert edited["reply_markup"] == {"inline_keyboard": []}


def prompted(bot: Bot, who: str) -> dict[str, Any]:
    """The message the reason is asked for in, as a reply quotes it."""
    [prompt] = [m for m in bot.telegram.sent("sendMessage") if "force_reply" in str(m)][-1:]
    assert prompt["chat_id"] == USERS[who]
    assert prompt["reply_markup"]["force_reply"] is True
    return {"message_id": bot.telegram.last_id, "chat": {"id": USERS[who], "type": "private"}}


async def test_rejecting_asks_for_the_reason_and_records_it(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    await bot.send(callback_query=click(card, "Rejeitar"))

    assert await decision(bot, proposal_id) is None
    reply_to = prompted(bot, "carla")

    await bot.send(
        message=message(
            user="carla",
            chat=USERS["carla"],
            text="  Quantidade errada. ",
            reply_to_message=reply_to,
        )
    )

    review = await decision(bot, proposal_id)
    assert review is not None
    assert (review.decision, review.reason) == ("rejected", "Quantidade errada.")
    [edited] = bot.telegram.sent("editMessageText")
    assert edited["message_id"] == 101
    assert edited["text"].endswith("Rejeição registrada: Quantidade errada.")
    assert bot.telegram.sent("sendMessage")[-1]["text"] == "Rejeição registrada."


async def test_the_reason_counts_only_from_the_reviewer_in_reply_to_the_prompt(bot: Bot) -> None:
    proposal_id, card = await carded(bot)
    await bot.send(callback_query=click(card, "Rejeitar"))
    prompt = prompted(bot, "carla")
    other = {"message_id": 99, "chat": {"id": USERS["carla"], "type": "private"}}

    for reply in [
        message(user="diego", chat=USERS["diego"], text="Não.", reply_to_message=prompt),
        message(user="carla", chat=USERS["carla"], text="Não.", reply_to_message=other),
        message(user="carla", chat=USERS["carla"], text="Não."),
        message(user="carla", chat=USERS["carla"], text=" ", reply_to_message=prompt),
    ]:
        await bot.send(message=reply)

    assert await decision(bot, proposal_id) is None
    await bot.send(message=message(user="carla", chat=USERS["carla"], reply_to_message=prompt))
    assert await decision(bot, proposal_id) is not None


async def test_a_contrary_decision_after_the_first_is_said_too_late(bot: Bot) -> None:
    proposal_id, card = await carded(bot)
    await bot.send(callback_query=click(card, "Aprovar"))
    diego = next(m for m in bot.telegram.sent("sendMessage") if m["chat_id"] == USERS["diego"])

    await bot.send(callback_query=click(diego, "Rejeitar", who="diego"))
    reply_to = prompted(bot, "diego")
    await bot.send(
        message=message(user="diego", chat=USERS["diego"], text="Não.", reply_to_message=reply_to)
    )

    assert bot.telegram.sent("sendMessage")[-1]["text"] == "Esta proposta já foi decidida."
    review = await decision(bot, proposal_id)
    assert review is not None and review.reviewer == "carla"


async def test_a_click_without_a_card_of_ours_is_refused_without_saying_why(bot: Bot) -> None:
    proposal_id, card = await carded(bot)
    [approve, _] = card["reply_markup"]["inline_keyboard"][0]
    verb, proposal, digest = approve["callback_data"].split(":")

    for query in [
        click(card, "Aprovar", who="erin"),
        click(card, "Aprovar", who="vera"),
        click(card, "Aprovar", data=f"{verb}:{proposal}:{'0' * len(digest)}"),
        click(card, "Aprovar", data=f"x:{proposal}:{digest}"),
        click(card, "Aprovar", data="garbage"),
        {**click(card, "Aprovar"), "data": None},
    ]:
        await bot.send(callback_query=query)
        assert answered(bot) == "Não foi possível registrar a decisão."

    assert await decision(bot, proposal_id) is None
    assert bot.telegram.sent("editMessageText") == []


async def test_a_click_by_who_may_not_review_is_refused(bot: Bot) -> None:
    proposal_id, card = await carded(bot)
    key = f"{proposal_id}:{USERS['carla']}"
    bot.cards[f"{proposal_id}:{USERS['frank']}"] = bot.cards[key]

    await bot.send(callback_query=click(card, "Aprovar", who="frank"))

    assert answered(bot) == "Não foi possível registrar a decisão."
    assert await decision(bot, proposal_id) is None


# Polling


async def eventually(condition: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while not await condition():
            await asyncio.sleep(0.01)


async def test_the_inbox_polls_from_its_offset_and_keeps_going_after_a_failure(bot: Bot) -> None:
    await bot.ask()
    telegram = bot.telegram
    telegram.updates = [[{"update_id": 40, "message": message()}]]

    async def polled(times: int) -> bool:
        return len(telegram.sent("getUpdates")) >= times

    polling = asyncio.create_task(bot.inbox.poll_forever(timeout=0, retry=0))
    await eventually(lambda: polled(2))
    telegram.fail = 502
    failing = len(telegram.sent("getUpdates"))
    await eventually(lambda: polled(failing + 2))
    telegram.fail = None
    telegram.updates = [[{"update_id": 41, "message": message(user="carla", message_id=7)}]]

    async def both_received() -> bool:
        return len(await bot.said()) == 2

    await eventually(both_received)
    polling.cancel()

    requests = telegram.sent("getUpdates")
    assert requests[0] == {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
    assert {r.get("offset") for r in requests[1:]} >= {41, 42}


async def test_an_update_whose_reply_telegram_refuses_does_not_hold_the_rest_back(
    bot: Bot,
) -> None:
    """A press replayed after a restart is stale, and Telegram refuses to answer it."""
    await bot.ask()
    _, card = await carded(bot)
    telegram = bot.telegram
    telegram.refused = {"answerCallbackQuery"}
    telegram.updates = [
        [
            {"update_id": 50, "callback_query": click(card, "Rejeitar")},
            {"update_id": 51, "message": message()},
        ]
    ]

    async def received() -> bool:
        return len(await bot.said()) == 1 and len(telegram.sent("getUpdates")) >= 2

    polling = asyncio.create_task(bot.inbox.poll_forever(timeout=0, retry=0))
    await eventually(received)
    polling.cancel()

    assert telegram.sent("getUpdates")[1]["offset"] == 52
    assert len([m for m in telegram.sent("sendMessage") if "force_reply" in str(m)]) == 1


async def test_the_app_polls_for_as_long_as_the_service_runs(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    telegram = Telegram()
    app = with_health(create_app(SETTINGS, pool, registry, telegram.client(), {}))
    inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    outbound: list[dict[str, Any]] = []

    async def send(event: dict[str, Any]) -> None:
        outbound.append(event)

    await inbound.put({"type": "lifespan.startup"})
    scope = {"type": "lifespan", "asgi": {"version": "3.0"}}

    async def run() -> None:
        await app(scope, inbound.get, send)

    running = asyncio.create_task(run())

    async def polling() -> bool:
        return bool(telegram.sent("getUpdates"))

    await eventually(polling)
    await inbound.put({"type": "lifespan.shutdown"})
    await asyncio.wait_for(running, 5)

    assert [e["type"] for e in outbound] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
