"""Exit criterion of slice 8 (PXOS-86): an agent's action goes through the policy.

The meeting agent acts for Bruno in autonomy phase 2. A commitment due within the
module's 14 days is recorded in the same call, without a card; one due later goes to the
project manager. After a redeploy that lowers the agent to phase 1, everything goes for
approval. Every call is in the audit.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from starlette.applications import Starlette

from db_testing import Database
from mcp_testing import BASE, call, running
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import build_server, http_app
from pxos_kernel.channel import FakeChannel, route_cards
from pxos_kernel.identity import install_identities, read_identities
from pxos_kernel.ontology import Registry
from pxos_kernel.state import create_pool, publish

WHEN = "2026-11-14T12:00:00+00:00"
QUOTE = "Bruno: a Célia revisa o contrato de dados."


def commitment(due_date: str) -> dict[str, str]:
    return {
        "description": "Revisar o contrato de dados do onboarding",
        "owner": "celia",
        "due_date": due_date,
        "work_stream": "onboarding-app",
    }


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


async def test_an_agent_action_goes_through_the_policy(
    database: Database,
    pool: asyncpg.Pool,
    registry: Registry,
    app: Starlette,
    idp: LocalIssuer,
    identities: Path,
    tmp_path: Path,
) -> None:
    publisher = await create_pool(database.publisher_dsn)
    owner = await asyncpg.connect(database.owner_dsn)
    channel = FakeChannel()
    token = idp.token("bruno", agent="meeting-agent")

    async def ask(due_date: str) -> dict[str, object]:
        arguments = {"payload": commitment(due_date), "quote": QUOTE, "valid_from": WHEN}
        result: dict[str, object] = await call(app, token, "create_commitment", arguments)
        return result

    async def cards() -> set[str]:
        await publish(publisher, {"proposal.created": route_cards(pool, registry, channel)})
        return set(channel.cards)

    try:
        await publish(publisher, {})  # events left by earlier tests are not ours

        # 1: within 14 days, phase 2 records the commitment in the same call, without a card.
        direct = await ask("2026-11-28")
        assert (direct["status"], direct["reason"]) == (
            "executed",
            "within rules in phase 2",
        )
        assert await cards() == set()
        listed = await call(app, idp.token("ana"), "list_commitments", {})
        assert direct["entity_id"] in {item["entity_id"] for item in listed["items"]}

        # 2: a later due date goes to the project manager.
        later = await ask("2026-11-29")
        assert (later["status"], later["reason"]) == ("pending", "due_date beyond 14 days")
        assert await cards() == {"ana"}

        # 3: a redeploy lowers the agent to phase 1, and everything goes for approval.
        lowered = tmp_path / "identities.yaml"
        content = identities.read_text(encoding="utf-8").replace("autonomy: 2", "autonomy: 1")
        lowered.write_text(content, encoding="utf-8")
        await install_identities(owner, read_identities(lowered))
        assisted = await ask("2026-11-20")
        assert (assisted["status"], assisted["reason"]) == ("pending", "autonomy phase 1")

        # 4: the audit holds every call, by the agent for Bruno.
        audit = await owner.fetch(
            "SELECT actor, principal, decision, result, proposal_id FROM pxos.action_audit"
            " WHERE proposal_id = ANY($1::uuid[]) ORDER BY id",
            [direct["proposal_id"], later["proposal_id"], assisted["proposal_id"]],
        )
        assert [(r["actor"], r["principal"], r["decision"], r["result"]) for r in audit] == [
            ("meeting-agent", "bruno", "allow", "executed"),
            ("meeting-agent", "bruno", "approval", "queued"),
            ("meeting-agent", "bruno", "approval", "queued"),
        ]
    finally:
        await install_identities(owner, read_identities(identities))
        await owner.close()
        await publisher.close()
