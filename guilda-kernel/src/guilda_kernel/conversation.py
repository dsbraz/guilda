"""Conversation in channel threads (ADR 0006, slice 11).

An agent posts in a thread for a cycle of work, an opaque reference; the messages that
follow in the thread belong to the cycle of the agent's last post there. A message from
the channel counts only from the internal person bound to its sender identity, written
in that person's session: the database sets the author and refuses anyone else, so a
stranger, a guest, an external actor or the agent's own echo adds nothing. A redelivered
channel message is kept once, and the post that carries a run's questions is posted
once. Each accepted message leaves a ``message.received`` event in the outbox, with the
message id and its cycle only.

A message, and the reason a reviewer rejected a proposal, are retained as context for the
next cycle with their author and the access tags of the agent's scope, read in the agent's
session and keyed by the message or the proposal, so retaining them again adds nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg

from guilda_kernel.channel import Channel
from guilda_kernel.context import ContextStore
from guilda_kernel.errors import GuildaError
from guilda_kernel.identity import Connection, NotAuthorized, bound_actor, session
from guilda_kernel.state import InvalidProposal, get_proposal, get_review


@dataclass(frozen=True)
class Inbound:
    """A message as the channel delivers it: who sent it is the channel's identity."""

    thread: str
    channel_message: str
    sender: str
    text: str


@dataclass(frozen=True)
class Message:
    id: UUID
    thread: str
    channel_message: str
    cycle: str
    sender: str | None
    author: str
    text: str
    received_at: datetime
    run: UUID | None = None
    """The run whose questions an agent's post carries."""


class NoCycle(GuildaError):
    """No agent has posted in the thread, so its messages belong to no cycle."""


async def post(
    pool: asyncpg.Pool,
    channel: Channel,
    agent: str,
    *,
    thread: str,
    cycle: str,
    text: str,
    run: UUID | None = None,
) -> Message:
    """Post ``text`` in ``thread`` as ``agent`` and keep it in the conversation of ``cycle``.

    The post of ``run`` is posted once: posting it again returns the kept message. A
    failure after the channel took the post and before the commit posts it again.
    """
    async with session(pool, agent) as connection:
        if run is not None:
            row = await connection.fetchrow("SELECT * FROM pxos.messages WHERE run = $1", run)
            if row is not None:
                return Message(**dict(row))
        channel_message = await channel.post(thread, text)
        return await _insert(connection, thread, channel_message, cycle, None, text, run)


async def receive(pool: asyncpg.Pool, inbound: Inbound) -> Message:
    """Keep ``inbound`` as a message of its bound sender, in the cycle of its thread."""
    async with pool.acquire() as connection:
        actor = await bound_actor(connection, inbound.sender)
    async with session(pool, actor) as connection:
        cycle = await connection.fetchval(
            """
            SELECT m.cycle FROM pxos.messages m JOIN pxos.actors a ON a.id = m.author
            WHERE m.thread = $1 AND a.kind = 'agent'
            ORDER BY m.received_at DESC LIMIT 1
            """,
            inbound.thread,
        )
        if cycle is None:
            raise NoCycle(f"no agent has posted in thread {inbound.thread}")
        return await _insert(
            connection, inbound.thread, inbound.channel_message, cycle, inbound.sender, inbound.text
        )


async def conversation(connection: Connection, cycle: str) -> list[Message]:
    """The messages of ``cycle``, oldest first, as the session can read them."""
    rows = await connection.fetch(
        "SELECT * FROM pxos.messages WHERE cycle = $1 ORDER BY received_at, id", cycle
    )
    return [Message(**dict(row)) for row in rows]


@dataclass(frozen=True)
class Answer:
    """A person's message in a cycle with the agent's post it followed: the question."""

    question: str
    message: Message


async def answers(connection: Connection, cycle: str) -> list[Answer]:
    """The messages people sent in ``cycle``, oldest first, each with the agent's post
    before it, as the session can read them."""
    rows = await connection.fetch(
        """
        SELECT q.text AS question, a.* FROM pxos.messages a
        CROSS JOIN LATERAL (
            SELECT text FROM pxos.messages
            WHERE cycle = a.cycle AND sender IS NULL AND received_at <= a.received_at
            ORDER BY received_at DESC, id DESC LIMIT 1
        ) q
        WHERE a.cycle = $1 AND a.sender IS NOT NULL
        ORDER BY a.received_at, a.id
        """,
        cycle,
    )
    return [
        Answer(question=row["question"], message=Message(**{k: row[k] for k in _FIELDS}))
        for row in rows
    ]


_FIELDS = tuple(Message.__dataclass_fields__)


async def retain_message(
    pool: asyncpg.Pool,
    store: ContextStore,
    agent: str,
    message_id: UUID,
    instructions: str,
    *,
    tags: Sequence[str],
) -> None:
    """Retain the message ``message_id`` with its author, read as ``agent``, under ``tags``."""
    async with session(pool, agent) as connection:
        row = await connection.fetchrow("SELECT * FROM pxos.messages WHERE id = $1", message_id)
    assert row is not None
    message = Message(**dict(row))
    await store.retain(
        f"message:{message.id}",
        f"Message from {message.author} in cycle {message.cycle}: {message.text}",
        instructions,
        tags=tags,
    )


async def retain_rejection(
    pool: asyncpg.Pool,
    store: ContextStore,
    agent: str,
    proposal_id: UUID,
    instructions: str,
    *,
    tags: Sequence[str],
) -> None:
    """Retain why ``proposal_id`` was rejected, with its reviewer, read as ``agent``,
    under ``tags``."""
    async with session(pool, agent) as connection:
        proposal = await get_proposal(connection, proposal_id)
        decided = await get_review(connection, proposal_id)
    if decided.decision != "rejected":
        raise InvalidProposal(f"proposal {proposal_id} was not rejected")
    await store.retain(
        f"rejection:{proposal_id}",
        f"Correction from {decided.reviewer}: proposal {proposal_id} ({proposal.type_name})"
        f" was rejected because: {decided.reason}",
        instructions,
        tags=tags,
    )


async def _insert(
    connection: Connection,
    thread: str,
    channel_message: str,
    cycle: str,
    sender: str | None,
    text: str,
    run: UUID | None = None,
) -> Message:
    try:
        row = await connection.fetchrow(
            """
            INSERT INTO pxos.messages (thread, channel_message, cycle, sender, text, run)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (thread, channel_message) DO NOTHING RETURNING *
            """,
            thread,
            channel_message,
            cycle,
            sender,
            text,
            run,
        )
    except asyncpg.PostgresError as error:
        if error.sqlstate == "PX019":
            raise NotAuthorized(str(error)) from None
        raise
    if row is None:
        row = await connection.fetchrow(
            "SELECT * FROM pxos.messages WHERE thread = $1 AND channel_message = $2",
            thread,
            channel_message,
        )
    assert row is not None
    return Message(**dict(row))
