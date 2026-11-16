"""The bot's messaging endpoint, end to end against the real database.

Only the Bot Connector's token for this bot and activity, from the configured tenant, gets
in. A message in the project's Teams conversation becomes a message of the kernel's
thread from ``teams:<Entra object id>``, accepted only from the internal person bound to
it. A click on a review card is that person's decision on the digest the card showed.
The endpoint answers without the message, the exception or the kernel's reason.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import retail_fixture.generated as retail_models
from teams_testing import AAD, CHANNEL, SETTINGS, TENANT, Microsoft, activity

from db_testing import Database, create_test_database
from model_testing import ROUTE
from pxos_kernel.channel import route_cards
from pxos_kernel.conversation import Message, conversation, post
from pxos_kernel.identity import Identities, install_identities, read_identities, session
from pxos_kernel.model_access import install_route
from pxos_kernel.ontology import Registry
from pxos_kernel.state import (
    Event,
    Evidence,
    InvalidProposal,
    Review,
    create_pool,
    get_review,
    install_packages,
    propose,
)
from pxos_teams import TeamsChannel
from pxos_teams.app import create_app

AGENT = "restock-agent"
IDENTITIES_FILE = Path(__file__).parent / "identities.yaml"
IDENTITIES: Identities = read_identities(IDENTITIES_FILE)


@pytest.fixture(scope="module")
def registry() -> Registry:
    registry = Registry()
    registry.register(Path(retail_models.__file__).parent, retail_models)
    return registry


@pytest.fixture(scope="module")
def database(registry: Registry) -> Database:
    """An installation of the kernel's retail fixture whose people are bound to Teams."""
    database = create_test_database("pxos_test_teams")

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
    """The endpoint and the channel of one project thread, over a faked Microsoft."""

    def __init__(
        self, pool: asyncpg.Pool, registry: Registry, conversation: str | None = None
    ) -> None:
        self.pool, self.registry = pool, registry
        self.thread, self.cycle = f"thread-{uuid4()}", f"cycle-{uuid4()}"
        self.conversation = conversation or f"19:{uuid4().hex}@thread.tacv2"
        self.microsoft = Microsoft()
        self.settings = replace(SETTINGS, threads={self.thread: self.conversation})
        self.channel = TeamsChannel(self.settings, self.microsoft.client(), IDENTITIES)
        app = create_app(self.settings, pool, registry, self.microsoft.client())
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://bot.example.test",
        )

    async def ask(self) -> Message:
        return await post(
            self.pool, self.channel, AGENT, thread=self.thread, cycle=self.cycle, text="Quantas?"
        )

    def message(self, **overrides: Any) -> dict[str, Any]:
        chain = {"id": f"{self.conversation};messageid=1700", "tenantId": TENANT}
        return activity(**{"conversation": chain, **overrides})

    async def send(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.microsoft.signer.token()}"}
        return await self.http.post("/api/messages", json=body, headers=headers)

    async def said(self) -> list[Message]:
        async with session(self.pool, AGENT) as connection:
            return [m for m in await conversation(connection, self.cycle) if m.sender]


@pytest.fixture
async def bot(pool: asyncpg.Pool, registry: Registry) -> Bot:
    return Bot(pool, registry)


async def test_a_reply_in_the_project_thread_is_received_from_its_sender(bot: Bot) -> None:
    question = await bot.ask()
    body = bot.message()

    response = await bot.send(body)

    assert (response.status_code, response.content) == (200, b"")
    [received] = await bot.said()
    assert (received.author, received.sender) == ("erin", f"teams:{AAD['erin']}")
    assert (received.text, received.channel_message) == (
        "Cinco caixas chegam na terça.",
        body["id"],
    )
    assert question.channel_message in str(bot.microsoft.conversations[bot.conversation])


async def test_a_thread_mapped_to_one_reply_chain_receives_from_it(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    bot = Bot(pool, registry, conversation=f"{CHANNEL};messageid={uuid4().int}")
    await bot.ask()

    await bot.send(activity(conversation={"id": bot.conversation}))

    assert len(await bot.said()) == 1


async def test_a_redelivered_message_is_kept_once(bot: Bot) -> None:
    await bot.ask()
    body = bot.message()

    await bot.send(body)
    await bot.send(body)

    assert len(await bot.said()) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"from": {"id": "29:x", "aadObjectId": AAD["vera"]}},
        {"from": {"id": "29:x", "aadObjectId": "aad-stranger"}},
        {"from": {"id": "29:x"}},
        {"text": "<at>pxOS</at>  "},
        {"conversation": {"id": "19:other@thread.tacv2;messageid=1"}},
        {"conversation": {"id": "a:personal-chat"}},
        {"type": "conversationUpdate"},
    ],
    ids=["external", "unbound", "no-entra-id", "only-mention", "other-channel", "chat", "event"],
)
async def test_anything_else_is_acknowledged_and_ignored(
    bot: Bot, overrides: dict[str, Any]
) -> None:
    await bot.ask()

    response = await bot.send(bot.message(**overrides))

    assert (response.status_code, response.content) == (200, b"")
    assert await bot.said() == []


async def test_a_message_before_any_question_belongs_to_no_cycle(bot: Bot) -> None:
    response = await bot.send(bot.message())

    assert (response.status_code, response.content) == (200, b"")


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer forged"}],
    ids=["no-token", "forged"],
)
async def test_a_call_without_the_connectors_token_is_refused(
    bot: Bot, headers: dict[str, str]
) -> None:
    await bot.ask()

    response = await bot.http.post("/api/messages", json=bot.message(), headers=headers)

    assert (response.status_code, response.content) == (401, b"")
    assert await bot.said() == []


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"], ids=["not-json", "not-an-activity"])
async def test_a_body_that_is_no_activity_is_refused(bot: Bot, body: bytes) -> None:
    headers = {"Authorization": f"Bearer {bot.microsoft.signer.token()}"}

    response = await bot.http.post("/api/messages", content=body, headers=headers)

    assert response.status_code == 401


async def test_a_call_from_another_tenant_is_refused(bot: Bot) -> None:
    await bot.ask()

    response = await bot.send(bot.message(channelData={"tenant": {"id": "other-tenant"}}))

    assert response.status_code == 401
    assert await bot.said() == []


async def test_an_unexpected_failure_answers_500_without_content(bot: Bot) -> None:
    await bot.ask()
    await bot.pool.close()

    response = await bot.send(bot.message())

    assert response.status_code == 500
    assert "Cinco" not in response.text and "pool" not in response.text.lower()


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
    """A proposal routed to the reviewers; Carla's card as Teams shows it."""
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
    [message] = bot.microsoft.conversations[f"a:{AAD['carla']}"]
    return proposal_id, message["attachments"][0]["content"]


def click(card: dict[str, Any], verb: str, who: str = "carla", **inputs: str) -> dict[str, Any]:
    """The invoke Teams sends when ``who`` clicks ``verb`` on ``card``."""
    [action] = [a for a in card["actions"] if a["verb"] == verb]
    return activity(
        type="invoke",
        name="adaptiveCard/action",
        conversation={"id": f"a:{AAD[who]}", "conversationType": "personal", "tenantId": TENANT},
        **{"from": {"id": "29:x", "aadObjectId": AAD[who]}},
        value={
            "action": {"type": "Action.Execute", "verb": verb, "data": {**action["data"], **inputs}}
        },
    )


async def decision(bot: Bot, proposal_id: UUID) -> Review | None:
    async with session(bot.pool, "carla") as connection:
        try:
            return await get_review(connection, proposal_id)
        except InvalidProposal:
            return None


def answer(response: httpx.Response) -> str:
    body = response.json()
    assert (body["statusCode"], body["type"]) == (200, "application/vnd.microsoft.activity.message")
    return str(body["value"])


async def test_approving_on_the_card_records_the_reviewers_decision(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    response = await bot.send(click(card, "approve"))

    assert answer(response) == "Aprovação registrada."
    review = await decision(bot, proposal_id)
    assert review is not None
    assert (review.decision, review.reviewer, review.reason) == ("approved", "carla", None)


async def test_rejecting_on_the_card_records_the_reason(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    response = await bot.send(click(card, "reject", reason="  Quantidade errada. "))

    assert answer(response) == "Rejeição registrada."
    review = await decision(bot, proposal_id)
    assert review is not None
    assert (review.decision, review.reason) == ("rejected", "Quantidade errada.")


async def test_a_rejection_without_a_reason_is_not_recorded(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    response = await bot.send(click(card, "reject", reason="  "))

    assert answer(response) == "Informe o motivo da rejeição."
    assert await decision(bot, proposal_id) is None


async def test_a_contrary_decision_after_the_first_is_said_too_late(bot: Bot) -> None:
    _, card = await carded(bot)
    await bot.send(click(card, "approve"))

    response = await bot.send(click(card, "reject", who="diego", reason="Não."))

    assert answer(response) == "Esta proposta já foi decidida."


@pytest.mark.parametrize("who", ["erin", "vera", "frank"], ids=["author", "external", "unbound"])
async def test_a_click_by_who_may_not_review_is_refused_without_saying_why(
    bot: Bot, who: str
) -> None:
    proposal_id, card = await carded(bot)

    response = await bot.send(click(card, "approve", who=who))

    assert answer(response) == "Não foi possível registrar a decisão."
    assert await decision(bot, proposal_id) is None


async def test_a_click_on_another_digest_is_refused(bot: Bot) -> None:
    proposal_id, card = await carded(bot)

    response = await bot.send(click(card, "approve", digest="0" * 64))

    assert answer(response) == "Não foi possível registrar a decisão."
    assert await decision(bot, proposal_id) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"action": {"verb": "delete", "data": {}}},
        {"action": {"verb": "approve", "data": {"proposal_id": "x", "digest": "d"}}},
    ],
    ids=["none", "empty", "unknown-verb", "bad-id"],
)
async def test_a_click_from_no_card_of_ours_is_refused(bot: Bot, value: Any) -> None:
    body = activity(type="invoke", name="adaptiveCard/action", value=value)

    assert answer(await bot.send(body)) == "Não foi possível registrar a decisão."
