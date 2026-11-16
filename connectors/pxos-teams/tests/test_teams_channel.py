"""The bot speaking first: posts in the project's thread and review cards in each
reviewer's personal chat, through the Bot Connector with the bot's own token.

Posts go to the Teams conversation the deploy maps the kernel's thread to. A card goes to
the recipient's 1:1 chat, addressed by the Entra object id bound to the actor as
``teams:<id>``; delivering it again replaces that same message.
"""

import json
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from teams_testing import AAD, APP_ID, CHANNEL, SETTINGS, TENANT, THREAD, Microsoft, new_key

from pxos_kernel.channel import Card
from pxos_kernel.identity import Actor, Identities
from pxos_teams import Certificate, TeamsChannel

IDENTITIES = Identities(
    actors=(
        Actor(id="carla", kind="internal_person", name="Carla", channel_ids=("teams:aad-carla",)),
        Actor(id="gina", kind="internal_person", name="Gina", channel_ids=("chat:gina",)),
    )
)
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"


def channel(microsoft: Microsoft, **settings: Any) -> TeamsChannel:
    from dataclasses import replace

    return TeamsChannel(replace(SETTINGS, **settings), microsoft.client(), IDENTITIES)


def card(recipient: str = "carla", **overrides: Any) -> Card:
    base: dict[str, Any] = {
        "proposal_id": uuid4(),
        "recipient": recipient,
        "package": "brq_weekly_report",
        "action": "propose_weekly_report",
        "title": "Report semanal",
        "payload": {
            "summary": "Semana estável.",
            "items": [
                {"category": "progress", "text": "API entregue.", "sources": ["message:1", "doc:2"]}
            ],
            "uncertainties": ["Data da homologação."],
        },
        "quote": "A API foi entregue na quarta.",
        "source": "agent-run:42",
        "digest": "d" * 64,
    }
    base.update(overrides)
    return Card(**base)


def form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


async def test_a_post_goes_to_the_teams_conversation_of_the_thread() -> None:
    microsoft = Microsoft()

    message = await channel(microsoft).post(THREAD, "Quando chega a homologação?")

    [sent] = microsoft.conversations[CHANNEL]
    assert (sent["type"], sent["text"], sent["id"]) == (
        "message",
        "Quando chega a homologação?",
        message,
    )
    [request] = microsoft.sent("POST", "/activities")
    assert request.url.raw_path.decode().startswith(
        "/teams/v3/conversations/19%3Aproject%40thread.tacv2/activities"
    )


async def test_the_bot_authenticates_with_its_secret_in_its_own_tenant() -> None:
    microsoft = Microsoft()

    await channel(microsoft).post(THREAD, "Oi")

    [token] = microsoft.sent("POST", "login.microsoftonline.com")
    assert str(token.url) == TOKEN_URL
    assert form(token) == {
        "grant_type": "client_credentials",
        "client_id": APP_ID,
        "client_secret": "s3cret",
        "scope": "https://api.botframework.com/.default",
    }
    [post] = microsoft.sent("POST", "/activities")
    assert post.headers["authorization"].startswith("Bearer tok-")


async def test_the_token_is_reused_until_it_nearly_expires() -> None:
    microsoft = Microsoft()
    teams = channel(microsoft)

    await teams.post(THREAD, "Um")
    await teams.post(THREAD, "Dois")

    assert len(microsoft.sent("POST", "login.microsoftonline.com")) == 1


async def test_the_bot_may_authenticate_with_a_certificate() -> None:
    microsoft = Microsoft()
    key = new_key()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    await channel(
        microsoft, secret=None, certificate=Certificate(private_key_pem=pem, thumbprint="ab" * 20)
    ).post(THREAD, "Oi")

    [token] = microsoft.sent("POST", "login.microsoftonline.com")
    sent = form(token)
    assert "client_secret" not in sent
    assert sent["client_assertion_type"] == (
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    )
    assertion = sent["client_assertion"]
    assert jwt.get_unverified_header(assertion)["x5t"] == "q6urq6urq6urq6urq6urq6urq6s"
    claims = jwt.decode(assertion, key.public_key(), algorithms=["RS256"], audience=TOKEN_URL)
    assert (claims["iss"], claims["sub"]) == (APP_ID, APP_ID)


def test_a_bot_needs_exactly_one_credential() -> None:
    microsoft = Microsoft()
    with pytest.raises(ValueError, match="secret or a certificate"):
        channel(microsoft, secret=None)
    with pytest.raises(ValueError, match="secret or a certificate"):
        channel(microsoft, certificate=Certificate(private_key_pem="", thumbprint=""))


async def test_a_thread_the_deploy_did_not_map_is_refused() -> None:
    with pytest.raises(KeyError):
        await channel(Microsoft()).post("another-project", "Oi")


async def test_a_connector_failure_raises_so_the_outbox_retries() -> None:
    microsoft = Microsoft()
    microsoft.fail = 503
    with pytest.raises(httpx.HTTPStatusError):
        await channel(microsoft).post(THREAD, "Oi")


async def test_a_card_goes_to_the_recipients_personal_chat() -> None:
    microsoft = Microsoft()

    await channel(microsoft).deliver_card(card())

    [create] = microsoft.sent("POST", "/v3/conversations")[:1]
    assert json.loads(create.content) == {
        "bot": {"id": f"28:{APP_ID}"},
        "members": [{"id": AAD["carla"]}],
        "tenantId": TENANT,
        "isGroup": False,
        "channelData": {"tenant": {"id": TENANT}},
    }
    [message] = microsoft.conversations[f"a:{AAD['carla']}"]
    [attachment] = message["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"


async def test_delivering_the_same_card_again_replaces_its_message() -> None:
    microsoft = Microsoft()
    teams = channel(microsoft)
    first = card()

    await teams.deliver_card(first)
    await teams.deliver_card(first)
    await teams.deliver_card(card())

    assert len(microsoft.conversations[f"a:{AAD['carla']}"]) == 2
    [replaced] = microsoft.sent("PUT", "/activities/act-0")
    assert json.loads(replaced.content)["attachments"]


async def test_the_card_messages_survive_a_new_channel_given_the_same_store() -> None:
    """The deploy passes a durable mapping (e.g. ``shelve``); a restart keeps replacing."""
    microsoft = Microsoft()
    store: dict[str, tuple[str, str]] = {}
    first = card()

    await TeamsChannel(SETTINGS, microsoft.client(), IDENTITIES, cards=store).deliver_card(first)
    await TeamsChannel(SETTINGS, microsoft.client(), IDENTITIES, cards=store).deliver_card(first)

    assert len(microsoft.conversations[f"a:{AAD['carla']}"]) == 1


async def test_a_recipient_without_a_teams_identity_gets_no_card(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Skipped and logged, so one unbound reviewer does not hold the whole outbox back."""
    microsoft = Microsoft()

    await channel(microsoft).deliver_card(card("gina"))

    assert microsoft.sent("POST", "/v3/conversations") == []
    assert "gina" in caplog.text
