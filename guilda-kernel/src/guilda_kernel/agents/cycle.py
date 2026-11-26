"""What a round of the agent's cycle reads, and the effort the cycle took (ADR 0006 §2).

A round reads the cycle so far as data, in the agent's session: each answer from the
thread with the question it followed, its author and its message, so an item can cite
``message:<id>``; the reason the reviewer gave for each rejected deliverable of the cycle,
citable as ``rejection:<id>``; and the sources the connectors could not reach. Corrections
from earlier cycles reach the agent through recall, in the version's scope.

The effort of a cycle is read back from its records: rounds, questions asked, answers,
rejections, the time from each proposal to the reviewer's decision, tokens and cost.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import asyncpg

from guilda_kernel.agents.runtime import RoundInput
from guilda_kernel.conversation import answers
from guilda_kernel.identity import Connection, session

_DELIVERED = """
    FROM guilda.agent_runs a
    JOIN guilda.proposals p ON p.evidence->>'source' = 'agent-run:' || a.id
    JOIN guilda.reviews r ON r.proposal_id = p.id
    WHERE a.cycle = $1
"""


async def round_input(
    pool: asyncpg.Pool, agent: str, cycle: str, *, final_round: bool
) -> RoundInput:
    """The data of the next round of ``cycle``, read as ``agent``."""
    async with session(pool, agent) as connection:
        answered = await answers(connection, cycle)
        rejected = await connection.fetch(
            f"SELECT p.id, r.reviewer, r.reason {_DELIVERED} AND r.decision = 'rejected'"
            " ORDER BY r.decided_at",
            cycle,
        )
        unavailable = await connection.fetch(
            "SELECT source FROM guilda.source_states WHERE state = 'unavailable' ORDER BY source"
        )
    return RoundInput(
        answers=tuple(
            f"Question: {a.question}\n"
            f"Answer from {a.message.author} (message:{a.message.id}): {a.message.text}"
            for a in answered
        ),
        corrections=tuple(
            f"Correction from {row['reviewer']} (rejection:{row['id']}): {row['reason']}"
            for row in rejected
        ),
        unavailable_sources=tuple(row["source"] for row in unavailable),
        final_round=final_round,
    )


@dataclass(frozen=True)
class Effort:
    rounds: int
    questions: int
    answers: int
    rejections: int
    review_time: timedelta
    """From each proposal of the cycle to the reviewer's decision, summed."""
    input_tokens: int
    output_tokens: int
    cost: Decimal


async def cycle_effort(connection: Connection, cycle: str) -> Effort:
    """The effort ``cycle`` took, as the session can read it."""
    runs = await connection.fetchrow(
        """
        SELECT count(*) AS rounds,
               coalesce(sum(jsonb_array_length(coalesce(output->'questions', '[]'))), 0)
                   AS questions,
               coalesce(sum(input_tokens), 0) AS input_tokens,
               coalesce(sum(output_tokens), 0) AS output_tokens,
               coalesce(sum(cost), 0) AS cost
        FROM guilda.agent_runs WHERE cycle = $1
        """,
        cycle,
    )
    reviews = await connection.fetchrow(
        "SELECT count(*) FILTER (WHERE r.decision = 'rejected') AS rejections,"
        f" coalesce(sum(r.decided_at - p.created_at), interval '0') AS review_time {_DELIVERED}",
        cycle,
    )
    people = await connection.fetchval(
        "SELECT count(*) FROM guilda.messages WHERE cycle = $1 AND sender IS NOT NULL", cycle
    )
    assert runs is not None and reviews is not None
    return Effort(answers=people, **dict(runs), **dict(reviews))
