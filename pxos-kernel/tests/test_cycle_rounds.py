"""What a round of the agent's cycle reads, posts and costs (ADR 0006 §2, slice 13).

A round reads the cycle so far as data: each answer from the thread with the question it
answers, its author and its message, so an item can cite ``message:<id>``; each reason
the reviewer gave to reject a deliverable of the cycle; and the sources the connectors
could not reach. A round's questions go to the version's thread in one post, once. The
cycle's effort is read back from what was recorded: rounds, questions, answers,
rejections, the time the reviewer took, tokens and cost.
"""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT

from model_testing import ROUTE
from pxos_kernel.agents import (
    AgentVersion,
    NoRecordedRun,
    RoundInput,
    approved_version,
    build_agent,
    cycle_effort,
    post_questions,
    propose_deliverable,
    round_input,
    run_round,
    scoped_recall,
)
from pxos_kernel.channel import FakeChannel
from pxos_kernel.context import FakeContextStore
from pxos_kernel.conversation import Inbound, conversation, receive
from pxos_kernel.identity import session
from pxos_kernel.ingestion import FakeConnector, SourceDocument, sync
from pxos_kernel.model_access import FakeModels, ModelGateway
from pxos_kernel.ontology import Registry
from pxos_kernel.state import review

ORDER = {"sku": "A-1", "quantity": 12, "occurred_at": "2026-11-14T09:00:00Z"}
ASKS = json.dumps({"questions": ["Which aisle?", "How many boxes?"], "deliverable": None})
DELIVERS = json.dumps({"questions": None, "deliverable": ORDER})


async def version_in_force(pool: asyncpg.Pool) -> AgentVersion:
    async with session(pool, AGENT) as connection:
        return await approved_version(connection, AGENT)


async def play(
    pool: asyncpg.Pool, registry: Registry, cycle: str, round: int, reply: str
) -> AgentVersion:
    """Run ``round`` of ``cycle`` with a model that answers ``reply``, 100 + 10 tokens."""
    version = await version_in_force(pool)
    model = await ModelGateway(pool, FakeModels(reply=reply, tokens=(100, 10))).adk_model()
    async with pool.acquire() as connection:
        recall = scoped_recall(FakeContextStore(), version)
        agent = await build_agent(connection, model, registry, version, recall=recall)
    await run_round(pool, agent, RoundInput(), cycle=cycle, round=round)
    return version


def said(thread: str, text: str) -> Inbound:
    return Inbound(thread=thread, channel_message=f"m-{uuid4()}", sender="chat:erin", text=text)


async def test_a_rounds_questions_are_posted_once_in_the_versions_thread(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    channel, cycle = FakeChannel(), f"cycle:{uuid4()}"
    version = await play(pool, registry, cycle, 1, ASKS)

    first = await post_questions(pool, channel, version, cycle=cycle, round=1)
    again = await post_questions(pool, channel, version, cycle=cycle, round=1)

    assert again == first
    assert channel.threads[version.thread] == ["Which aisle?\nHow many boxes?"]
    async with session(pool, AGENT) as connection:
        assert await conversation(connection, cycle) == [first]


async def test_only_a_round_that_asked_posts(pool: asyncpg.Pool, registry: Registry) -> None:
    cycle = f"cycle:{uuid4()}"
    version = await version_in_force(pool)
    with pytest.raises(NoRecordedRun):
        await post_questions(pool, FakeChannel(), version, cycle=cycle, round=1)
    await play(pool, registry, cycle, 1, DELIVERS)
    with pytest.raises(NoRecordedRun):
        await post_questions(pool, FakeChannel(), version, cycle=cycle, round=1)


async def test_a_round_reads_the_cycle_so_far_and_the_cycle_its_effort(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    version = await play(pool, registry, cycle, 1, ASKS)
    await post_questions(pool, FakeChannel(), version, cycle=cycle, round=1)
    answer = await receive(pool, said(version.thread, "Aisle 3, twelve boxes."))
    await play(pool, registry, cycle, 2, DELIVERS)
    proposal = await propose_deliverable(pool, registry, version, cycle=cycle, round=2)
    async with session(pool, "carla") as connection:
        await review(
            connection, registry, proposal.id, "rejected", digest=proposal.digest, reason="Ten."
        )
    shelf, unreachable = FakeConnector(f"fixture-{uuid4()}"), f"fixture:{uuid4()}"
    shelf.put(SourceDocument(source=unreachable, version="1", text="x", access=()))
    await sync(pool, FakeContextStore(), shelf, agent=AGENT)
    shelf.available = False
    await sync(pool, FakeContextStore(), shelf, agent=AGENT)

    read = await round_input(pool, AGENT, cycle, final_round=True)

    assert read.answers == (
        f"Question: Which aisle?\nHow many boxes?\n"
        f"Answer from erin (message:{answer.id}): Aisle 3, twelve boxes.",
    )
    assert read.corrections == (f"Correction from carla (rejection:{proposal.id}): Ten.",)
    assert unreachable in read.unavailable_sources
    assert read.final_round is True

    async with session(pool, AGENT) as connection:
        effort = await cycle_effort(connection, cycle)
        decided = await connection.fetchval(
            "SELECT r.decided_at - p.created_at FROM pxos.reviews r"
            " JOIN pxos.proposals p ON p.id = r.proposal_id WHERE p.id = $1",
            proposal.id,
        )
    assert (effort.rounds, effort.questions, effort.answers, effort.rejections) == (2, 2, 1, 1)
    assert effort.review_time == decided > timedelta(0)
    assert (effort.input_tokens, effort.output_tokens) == (200, 20)
    assert effort.cost == (200 * ROUTE.input_price + 20 * ROUTE.output_price) / 1_000_000


async def test_a_cycle_that_did_not_run_took_no_effort(pool: asyncpg.Pool) -> None:
    async with session(pool, AGENT) as connection:
        effort = await cycle_effort(connection, f"cycle:{uuid4()}")
    assert (effort.rounds, effort.questions, effort.answers, effort.rejections) == (0, 0, 0, 0)
    assert (effort.review_time, effort.input_tokens, effort.cost) == (timedelta(0), 0, Decimal(0))
