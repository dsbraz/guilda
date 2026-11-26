"""Channel port and review cards (K55).

A created proposal becomes a card for each actor who holds the action's approver role,
except its author and whoever the author acts for. The card is read in the recipient's
session, so the database decides whether they see it at all: RLS hides it, and an
external actor gets no session, since a card shows internal context (K61). Delivery
is keyed by proposal: a redelivered event replaces the same card. Deciding on a card is a
review, on exactly the digest the card showed, by the actor bound to the channel identity
that clicked; the database decides whether that actor may review.
"""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import asyncpg

from guilda_kernel.identity import NotAuthorized, bound_actor, session
from guilda_kernel.ontology import Registry
from guilda_kernel.state import (
    Decision,
    Event,
    Handler,
    InvalidProposal,
    Review,
    get_proposal,
    review,
)


@dataclass(frozen=True)
class Card:
    proposal_id: UUID
    recipient: str
    package: str
    action: str
    title: str
    payload: dict[str, Any]
    quote: str
    source: str
    digest: str


class Channel(Protocol):
    async def deliver_card(self, card: Card) -> None:
        """Show ``card`` to its recipient; delivering the same card again replaces it."""

    async def post(self, thread: str, text: str) -> str:
        """Post ``text`` in ``thread``; return the channel's id of the new message."""


def route_cards(pool: asyncpg.Pool, registry: Registry, channel: Channel) -> Handler:
    """Handler for ``proposal.created`` that delivers a card to each eligible approver."""

    async def route(event: Event) -> None:
        proposal_id = UUID(event.payload["proposal_id"])
        registered = registry.get(event.payload["package"])
        async with pool.acquire() as connection:
            approver = registered.package.action(event.payload["action"]).approver
            candidates = await connection.fetch(
                """
                SELECT r.actor_id FROM guilda.actor_roles r
                JOIN guilda.actors a ON a.id = r.actor_id
                WHERE r.role = $1 AND a.kind = 'internal_person' ORDER BY r.actor_id
                """,
                approver,
            )
        for row in candidates:
            recipient = row["actor_id"]
            try:
                async with session(pool, recipient) as connection:
                    proposal = await get_proposal(connection, proposal_id)
            except (NotAuthorized, InvalidProposal):
                continue  # the recipient cannot read this proposal
            if recipient in {proposal.author, proposal.author_principal}:
                continue
            await channel.deliver_card(
                Card(
                    proposal_id=proposal.id,
                    recipient=recipient,
                    package=proposal.package,
                    action=proposal.action,
                    title=registered.package.schema.get_class(proposal.type_name).title
                    or proposal.type_name,
                    payload=proposal.payload,
                    quote=proposal.evidence["quote"],
                    source=proposal.evidence["source"],
                    digest=proposal.digest,
                )
            )

    return route


async def decide(
    pool: asyncpg.Pool,
    registry: Registry,
    clicker: str,
    card: Card,
    decision: Decision,
    *,
    reason: str | None = None,
) -> Review:
    """Record the decision of the actor bound to the channel identity ``clicker`` on the
    proposal exactly as ``card`` showed it."""
    async with pool.acquire() as connection:
        actor = await bound_actor(connection, clicker)
    async with session(pool, actor) as connection:
        return await review(
            connection, registry, card.proposal_id, decision, digest=card.digest, reason=reason
        )
