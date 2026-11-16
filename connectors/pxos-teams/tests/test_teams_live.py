"""The adapter against a real Azure Bot and Teams tenant; opt-in.

Set PXOS_TEAMS_APP_ID, PXOS_TEAMS_TENANT_ID, PXOS_TEAMS_SECRET, PXOS_TEAMS_SERVICE_URL,
PXOS_TEAMS_CONVERSATION (a channel or reply chain where the app is installed) and
PXOS_TEAMS_USER (the Entra object id of someone with the app installed in personal scope).
It posts a message in the conversation and a test card, delivered twice, to the user.
"""

import os
from uuid import uuid4

import httpx
import pytest

from pxos_kernel.channel import Card
from pxos_kernel.identity import Actor, Identities
from pxos_teams import Settings, TeamsChannel
from pxos_teams.auth import OPENID

ENV = {
    name: os.environ.get(f"PXOS_TEAMS_{name}", "")
    for name in ["APP_ID", "TENANT_ID", "SECRET", "SERVICE_URL", "CONVERSATION", "USER"]
}
pytestmark = pytest.mark.skipif(not all(ENV.values()), reason="PXOS_TEAMS_* not set")


async def test_the_bot_posts_and_replaces_a_card_in_the_real_tenant() -> None:
    settings = Settings(
        app_id=ENV["APP_ID"],
        tenant_id=ENV["TENANT_ID"],
        service_url=ENV["SERVICE_URL"],
        threads={"live": ENV["CONVERSATION"]},
        secret=ENV["SECRET"],
    )
    identities = Identities(
        actors=(
            Actor(
                id="pm", kind="internal_person", name="PM", channel_ids=(f"teams:{ENV['USER']}",)
            ),
        )
    )
    card = Card(
        proposal_id=uuid4(),
        recipient="pm",
        package="live",
        action="live",
        title="Teste do adaptador Teams",
        payload={"summary": "Pode ignorar."},
        quote="—",
        source="test_teams_live",
        digest="0" * 64,
    )
    async with httpx.AsyncClient(timeout=30) as http:
        teams = TeamsChannel(settings, http, identities)
        assert await teams.post("live", "Teste do adaptador Teams do pxOS; pode ignorar.")
        await teams.deliver_card(card)
        await teams.deliver_card(card)


async def test_the_bot_framework_keys_are_endorsed_for_teams() -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        metadata = (await http.get(OPENID)).json()
        keys = (await http.get(metadata["jwks_uri"])).json()["keys"]
    assert "RS256" in metadata["id_token_signing_alg_values_supported"]
    assert any("msteams" in key.get("endorsements", []) for key in keys)
