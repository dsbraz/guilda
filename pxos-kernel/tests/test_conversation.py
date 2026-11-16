"""Conversation in channel threads (ADR 0006, slice 11).

An agent posts in a thread for a cycle of work; the messages that follow in the thread
belong to the cycle of the agent's last post there. A received message counts only from
an internal person bound to its sender identity, written as that person. The refusals
below the kernel's API are sent as raw SQL by the application login, so a pass means the
database refused.

What the team says in the thread and why a reviewer rejected a proposal become context for
the next cycle, with their author, each under a key that retaining again does not repeat.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from db_testing import Database
from pxos_kernel.channel import FakeChannel
from pxos_kernel.context import FakeContextStore
from pxos_kernel.conversation import (
    Inbound,
    NoCycle,
    conversation,
    post,
    receive,
    retain_message,
    retain_rejection,
)
from pxos_kernel.identity import NotAuthorized, session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Decision, Evidence, InvalidProposal, Proposal, propose, review

AGENT = "restock-agent"
INSTRUCTIONS = "Keep each correction with who made it."


class TextStore(FakeContextStore):
    """The reference store, also keeping the text retained under each key."""

    def __init__(self) -> None:
        super().__init__()
        self.texts: dict[str, str] = {}

    async def retain(self, key: str, text: str, instructions: str, **options: Any) -> None:
        await super().retain(key, text, instructions, **options)
        self.texts[key] = text


def fresh(name: str) -> str:
    return f"{name}-{uuid4()}"


def answer(thread: str, sender: str = "chat:erin", text: str = "Cinco caixas.") -> Inbound:
    return Inbound(thread=thread, channel_message=fresh("m"), sender=sender, text=text)


async def test_a_question_is_posted_in_the_thread_and_kept_with_its_cycle(
    pool: asyncpg.Pool,
) -> None:
    channel = FakeChannel()
    thread, cycle = fresh("thread"), fresh("cycle")

    question = await post(pool, channel, AGENT, thread=thread, cycle=cycle, text="Quantas?")

    assert channel.threads[thread] == ["Quantas?"]
    assert (question.author, question.cycle, question.sender) == (AGENT, cycle, None)


async def test_an_answer_joins_the_cycle_of_the_agents_last_post_in_the_thread(
    pool: asyncpg.Pool,
) -> None:
    channel = FakeChannel()
    thread, earlier, cycle = fresh("thread"), fresh("cycle"), fresh("cycle")
    await post(pool, channel, AGENT, thread=thread, cycle=earlier, text="Semana passada?")
    question = await post(pool, channel, AGENT, thread=thread, cycle=cycle, text="Quantas?")

    reply = await receive(pool, answer(thread))

    assert (reply.author, reply.cycle, reply.sender) == ("erin", cycle, "chat:erin")
    async with session(pool, AGENT) as connection:
        assert await conversation(connection, cycle) == [question, reply]
        assert reply not in await conversation(connection, earlier)


async def test_a_redelivered_message_is_kept_once_with_one_event_of_ids_only(
    pool: asyncpg.Pool, database: Database
) -> None:
    thread, cycle = fresh("thread"), fresh("cycle")
    question = await post(pool, FakeChannel(), AGENT, thread=thread, cycle=cycle, text="Quantas?")
    inbound = answer(thread)

    first = await receive(pool, inbound)
    again = await receive(pool, inbound)

    assert again == first
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        events = await owner.fetch(
            "SELECT event_type, entity_id, payload FROM pxos.outbox WHERE entity_id = ANY($1)",
            [question.id, first.id],
        )
    finally:
        await owner.close()
    assert [(e["event_type"], e["entity_id"], json.loads(e["payload"])) for e in events] == [
        ("message.received", first.id, {"message_id": str(first.id), "cycle": cycle})
    ]


@pytest.mark.parametrize(
    "sender",
    ["chat:guest", "chat:vera", "chat:restock-agent"],
    ids=["unbound", "external", "the-agent-itself"],
)
async def test_a_message_from_anyone_but_a_bound_person_is_refused(
    pool: asyncpg.Pool, sender: str
) -> None:
    thread, cycle = fresh("thread"), fresh("cycle")
    await post(pool, FakeChannel(), AGENT, thread=thread, cycle=cycle, text="Quantas?")

    with pytest.raises(NotAuthorized):
        await receive(pool, answer(thread, sender=sender))

    async with session(pool, AGENT) as connection:
        assert [m.author for m in await conversation(connection, cycle)] == [AGENT]


async def test_a_message_in_a_thread_without_a_post_of_the_agent_is_refused(
    pool: asyncpg.Pool,
) -> None:
    with pytest.raises(NoCycle):
        await receive(pool, answer(fresh("thread")))


async def insert_as(pool: asyncpg.Pool, actor: str, on_behalf_of: str, sender: str | None) -> UUID:
    """Raw SQL: what a buggy application could send for a message."""
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT set_config('pxos.actor', $1, true), set_config('pxos.on_behalf_of', $2, true)",
            actor,
            on_behalf_of,
        )
        message_id: UUID = await connection.fetchval(
            """
            INSERT INTO pxos.messages (thread, channel_message, cycle, sender, text)
            VALUES ($1, $2, 'cycle', $3, 'texto') RETURNING id
            """,
            fresh("thread"),
            fresh("m"),
            sender,
        )
        return message_id


@pytest.mark.parametrize(
    ("actor", "on_behalf_of", "sender", "sqlstate"),
    [
        ("", "", "chat:erin", "PX001"),
        ("erin", "", "chat:guest", "PX019"),
        ("erin", "", "chat:carla", "PX019"),
        ("erin", "", None, "PX019"),
        ("vera", "", "chat:vera", "PX003"),
        ("restock-agent", "", "chat:erin", "PX019"),
        ("restock-agent", "erin", "chat:erin", "PX019"),
        ("restock-agent", "erin", None, "PX019"),
    ],
    ids=[
        "no-actor",
        "unbound-sender",
        "someone-elses-sender",
        "person-without-sender",
        "external",
        "agent-as-a-person",
        "agent-for-a-person",
        "agent-post-for-a-person",
    ],
)
async def test_the_database_refuses_a_message_not_written_by_its_bound_sender(
    pool: asyncpg.Pool, actor: str, on_behalf_of: str, sender: str | None, sqlstate: str
) -> None:
    with pytest.raises(asyncpg.PostgresError) as error:
        await insert_as(pool, actor, on_behalf_of, sender)
    assert error.value.sqlstate == sqlstate


async def test_the_author_comes_from_the_session(pool: asyncpg.Pool) -> None:
    message_id = await insert_as(pool, "erin", "", "chat:erin")
    async with session(pool, AGENT) as connection:
        author = await connection.fetchval(
            "SELECT author FROM pxos.messages WHERE id = $1", message_id
        )
    assert author == "erin"

    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError) as error:
            await connection.fetch("SELECT * FROM pxos.messages")
    assert error.value.sqlstate == "PX001"


async def test_a_message_is_retained_with_its_author_under_a_stable_key(
    pool: asyncpg.Pool,
) -> None:
    thread, cycle = fresh("thread"), fresh("cycle")
    await post(pool, FakeChannel(), AGENT, thread=thread, cycle=cycle, text="Quantas?")
    reply = await receive(pool, answer(thread, text="Na verdade foram sete."))
    store = TextStore()

    await retain_message(pool, store, AGENT, reply.id, INSTRUCTIONS)
    await retain_message(pool, store, AGENT, reply.id, INSTRUCTIONS)

    key = f"message:{reply.id}"
    assert store.retained == [(key, INSTRUCTIONS), (key, INSTRUCTIONS)]
    assert store.texts[key] == f"Message from erin in cycle {cycle}: Na verdade foram sete."


async def decided(
    pool: asyncpg.Pool, registry: Registry, decision: Decision | None, reason: str | None
) -> Proposal:
    async with session(pool, AGENT) as connection:
        proposal = await propose(
            connection,
            registry.get("retail"),
            "create_restock_order",
            payload={"sku": "B-2", "quantity": 4, "occurred_at": "2026-11-01T09:00:00Z"},
            evidence=Evidence(source="fixture:log", quote="restock 4", context_ref=fresh("ctx")),
            valid_from=datetime(2026, 11, 1, tzinfo=UTC),
        )
    if decision is not None:
        async with session(pool, "carla") as connection:
            await review(
                connection, registry, proposal.id, decision, digest=proposal.digest, reason=reason
            )
    return proposal


async def test_a_rejection_reason_is_retained_as_a_correction_by_its_reviewer(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    proposal = await decided(pool, registry, "rejected", "Eram sete, não quatro.")
    store = TextStore()

    await retain_rejection(pool, store, AGENT, proposal.id, INSTRUCTIONS)
    await retain_rejection(pool, store, AGENT, proposal.id, INSTRUCTIONS)

    key = f"rejection:{proposal.id}"
    assert store.retained == [(key, INSTRUCTIONS), (key, INSTRUCTIONS)]
    assert store.texts[key] == (
        f"Correction from carla: proposal {proposal.id} (RestockOrder) was rejected"
        " because: Eram sete, não quatro."
    )


@pytest.mark.parametrize(
    ("decision", "refusal"), [("approved", "not rejected"), (None, "has no review")]
)
async def test_only_a_rejection_is_a_correction(
    pool: asyncpg.Pool, registry: Registry, decision: Decision | None, refusal: str
) -> None:
    proposal = await decided(pool, registry, decision, None)
    store = TextStore()

    with pytest.raises(InvalidProposal, match=refusal):
        await retain_rejection(pool, store, AGENT, proposal.id, INSTRUCTIONS)
    assert store.retained == []
