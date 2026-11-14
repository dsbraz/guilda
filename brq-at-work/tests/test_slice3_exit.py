"""Exit criterion of slice 3 (PXOS-60): the team reads and proposes commitments over MCP.

Everything goes through the kernel's MCP app over HTTP with signed tokens, so each call
runs as the token's actor and the database decides what that actor sees.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest
from starlette.applications import Starlette

from db_testing import Database
from mcp_testing import BASE, ToolError, call, post_status, running
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import build_server, http_app
from pxos_kernel.channel import FakeChannel, decide, route_cards
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool, publish

COMMITMENT = {
    "description": "Revisar o contrato de dados do onboarding",
    "owner": "celia",
    "due_date": "2026-11-21",
    "work_stream": "onboarding-app",
}
QUOTE = "Célia: eu reviso o contrato de dados até o dia 21."


@pytest.fixture(scope="module")
def idp() -> LocalIssuer:
    return LocalIssuer.generate(issuer="http://idp.brq.test", audience="brq-at-work")


@pytest.fixture
async def app(pool: asyncpg.Pool, registry: Registry, idp: LocalIssuer) -> AsyncIterator[Starlette]:
    verifier = JwtVerifier(issuer=idp.issuer, audience=idp.audience, jwks=idp.jwks())
    app = http_app(
        build_server(pool, registry, name="brq-at-work"),
        verifier,
        pool=pool,
        issuer_url=idp.issuer,
        resource_url=f"{BASE}/mcp",
    )
    async with running(app):
        yield app


async def test_commitments_are_read_and_proposed_over_mcp(
    database: Database, pool: asyncpg.Pool, registry: Registry, app: Starlette, idp: LocalIssuer
) -> None:
    publisher = await create_pool(database.publisher_dsn)
    channel = FakeChannel()
    try:
        await publish(publisher, {})  # events left by earlier tests are not ours

        # 1: no token, or a token from another provider, is refused.
        foreign = LocalIssuer.generate(issuer=idp.issuer, audience=idp.audience)
        assert await post_status(app, None) == 401
        assert await post_status(app, foreign.token("ana")) == 401

        # 2: Célia proposes; the evidence source is her call, and the card goes to Ana.
        created = await call(
            app, idp.token("celia"), "create_commitment", {"payload": COMMITMENT, "quote": QUOTE}
        )
        assert (created["status"], created["approver_role"]) == ("pending", "project-manager")
        await publish(publisher, {"proposal.created": route_cards(pool, registry, channel)})
        assert set(channel.cards) == {"ana"}
        card = channel.cards["ana"][next(iter(channel.cards["ana"]))]
        assert (card.source, card.quote) == ("mcp:celia", QUOTE)

        # 3: a commitment whose owner nobody knows is refused.
        with pytest.raises(ToolError, match="unknown actor fulano"):
            await call(
                app,
                idp.token("celia"),
                "create_commitment",
                {"payload": {**COMMITMENT, "owner": "fulano"}, "quote": QUOTE},
            )

        # 4: once Ana approves, Ana, Célia and the agent acting for Bruno all list it.
        await decide(pool, registry, "ana", card, "approved")
        for person, agent in [("ana", None), ("celia", None), ("bruno", "meeting-agent")]:
            listed = await call(app, idp.token(person, agent=agent), "list_commitments", {})
            mine = [item for item in listed["items"] if item["entity_id"] == created["entity_id"]]
            assert [item["payload"] for item in mine] == [COMMITMENT], (person, agent)

        # 5: the timeline shows the revision with its evidence.
        timeline = await call(
            app, idp.token("ana"), "timeline", {"entity_id": created["entity_id"]}
        )
        assert [(r["revision"], r["evidence"]["quote"]) for r in timeline["items"]] == [(1, QUOTE)]
    finally:
        await publisher.close()
