"""Who may call the bot: only the Bot Connector, for this bot, about the activity it signs.

The token must be a Bearer JWT signed with RS256 by a key in the Bot Framework OpenID
keys, endorsed for the activity's channel, issued by the Bot Connector, addressed to the
bot's app id, within its validity (5 minutes of skew) and carrying the activity's
serviceUrl. Anything else is refused, whatever else the token says.
"""

import time
from typing import Any

import jwt
import pytest
from teams_testing import APP_ID, SERVICE_URL, Microsoft, Signer, activity

from pxos_teams.auth import Authenticator, Unauthorized


@pytest.fixture
def microsoft() -> Microsoft:
    return Microsoft()


def authenticator(microsoft: Microsoft, clock: Any = time.monotonic) -> Authenticator:
    return Authenticator(microsoft.client(), APP_ID, clock=clock)


async def test_a_token_from_the_bot_connector_for_this_bot_is_accepted(
    microsoft: Microsoft,
) -> None:
    await authenticator(microsoft).verify(f"Bearer {microsoft.signer.token()}", activity())


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic abc", "Bearer", "Bearer not-a-jwt", "bearer x.y.z"],
)
async def test_a_missing_or_malformed_authorization_is_refused(
    microsoft: Microsoft, header: str | None
) -> None:
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(header, activity())


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-bot"},
        {"aud": None},
        {"iss": "https://sts.windows.net/attacker/"},
        {"iss": None},
        {"exp": int(time.time()) - 301},
        {"exp": None},
        {"nbf": int(time.time()) + 3600},
        {"serviceurl": "https://attacker.example/"},
        {"serviceurl": None},
    ],
    ids=[
        "wrong-audience",
        "no-audience",
        "wrong-issuer",
        "no-issuer",
        "expired",
        "no-expiry",
        "not-yet-valid",
        "other-service-url",
        "no-service-url",
    ],
)
async def test_a_token_with_wrong_claims_is_refused(
    microsoft: Microsoft, claims: dict[str, Any]
) -> None:
    token = microsoft.signer.token(**claims)
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(f"Bearer {token}", activity())


async def test_clock_skew_of_five_minutes_is_tolerated(microsoft: Microsoft) -> None:
    token = microsoft.signer.token(exp=int(time.time()) - 200, nbf=int(time.time()) + 200)
    await authenticator(microsoft).verify(f"Bearer {token}", activity())


async def test_the_service_url_claim_may_come_camel_cased(microsoft: Microsoft) -> None:
    token = microsoft.signer.token(serviceurl=None, serviceUrl=SERVICE_URL)
    await authenticator(microsoft).verify(f"Bearer {token}", activity())


async def test_the_service_url_must_be_the_activitys(microsoft: Microsoft) -> None:
    token = microsoft.signer.token()
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(
            f"Bearer {token}", activity(serviceUrl="https://attacker.example/")
        )


async def test_a_forged_signature_under_a_published_key_id_is_refused(
    microsoft: Microsoft,
) -> None:
    forger = Signer(kid=microsoft.signer.kid)
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(f"Bearer {forger.token()}", activity())


@pytest.mark.parametrize("algorithm", ["HS256", "none"])
async def test_a_token_not_signed_with_rs256_is_refused(
    microsoft: Microsoft, algorithm: str
) -> None:
    """HS256 keyed with the public key, or no signature at all, is the classic forgery."""
    key: Any = microsoft.signer.jwk()["n"] if algorithm == "HS256" else None
    claims: dict[str, Any] = {
        "iss": "https://api.botframework.com",
        "aud": APP_ID,
        "serviceurl": SERVICE_URL,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": microsoft.signer.kid})
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(f"Bearer {token}", activity())


async def test_a_key_not_endorsed_for_the_channel_is_refused(microsoft: Microsoft) -> None:
    microsoft.signers = [Signer(endorsements=("webchat",))]
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(f"Bearer {microsoft.signer.token()}", activity())


async def test_an_activity_of_another_channel_needs_that_endorsement(
    microsoft: Microsoft,
) -> None:
    with pytest.raises(Unauthorized):
        await authenticator(microsoft).verify(
            f"Bearer {microsoft.signer.token()}", activity(channelId="webchat")
        )


async def test_keys_are_cached_and_a_new_key_id_refetches_them(microsoft: Microsoft) -> None:
    now = [0.0]
    auth = authenticator(microsoft, clock=lambda: now[0])
    await auth.verify(f"Bearer {microsoft.signer.token()}", activity())
    await auth.verify(f"Bearer {microsoft.signer.token()}", activity())
    assert len(microsoft.sent("GET", "/keys")) == 1

    rotated = Signer()
    microsoft.signers.append(rotated)
    now[0] = 301.0
    await auth.verify(f"Bearer {rotated.token()}", activity())
    assert len(microsoft.sent("GET", "/keys")) == 2


async def test_unknown_key_ids_do_not_refetch_more_than_every_five_minutes(
    microsoft: Microsoft,
) -> None:
    """A flood of tokens under made-up key ids cannot make the bot hammer Microsoft."""
    now = [0.0]
    auth = authenticator(microsoft, clock=lambda: now[0])
    await auth.verify(f"Bearer {microsoft.signer.token()}", activity())
    for _ in range(3):
        now[0] += 10
        with pytest.raises(Unauthorized):
            await auth.verify(f"Bearer {Signer().token()}", activity())
    assert len(microsoft.sent("GET", "/keys")) == 1


async def test_keys_are_refreshed_at_least_daily(microsoft: Microsoft) -> None:
    now = [0.0]
    auth = authenticator(microsoft, clock=lambda: now[0])
    await auth.verify(f"Bearer {microsoft.signer.token()}", activity())
    now[0] = 24 * 3600 + 1
    await auth.verify(f"Bearer {microsoft.signer.token()}", activity())
    assert len(microsoft.sent("GET", "/keys")) == 2
