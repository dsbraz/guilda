"""Bearer tokens (PXOS-57): a JWT from the installation's identity provider names the caller.

Only asymmetric signatures from the configured keys count, and exp, iss and aud are
required. The subject is the actor; an agent acting for a person carries the RFC 8693
``act`` claim (sub = person, act.sub = agent). Whether that actor or delegation exists is
for the database session to decide.
"""

import time
from typing import Any

import jwt
import pytest

from pxos_kernel.api.auth import Caller, JwtVerifier, LocalIssuer

ISSUER = "http://idp.test"
AUDIENCE = "pxos-test"


@pytest.fixture(scope="module")
def issuer() -> LocalIssuer:
    return LocalIssuer.generate(issuer=ISSUER, audience=AUDIENCE)


@pytest.fixture(scope="module")
def verifier(issuer: LocalIssuer) -> JwtVerifier:
    return JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=issuer.jwks())


async def test_a_person_token_names_the_person(issuer: LocalIssuer, verifier: JwtVerifier) -> None:
    access = await verifier.verify_token(issuer.token("erin"))
    assert access is not None
    assert Caller.from_token(access) == Caller(actor="erin", on_behalf_of=None)


async def test_an_agent_acting_for_a_person_names_both(
    issuer: LocalIssuer, verifier: JwtVerifier
) -> None:
    access = await verifier.verify_token(issuer.token("erin", agent="restock-agent"))
    assert access is not None
    assert Caller.from_token(access) == Caller(actor="restock-agent", on_behalf_of="erin")


def signed(issuer: LocalIssuer, **changes: Any) -> str:
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "erin", "exp": int(time.time()) + 60}
    claims.update(changes)
    return issuer.sign({key: value for key, value in claims.items() if value is not None})


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"exp": int(time.time()) - 60}, id="expired"),
        pytest.param({"exp": None}, id="no-exp"),
        pytest.param({"aud": "other"}, id="wrong-audience"),
        pytest.param({"aud": None}, id="no-audience"),
        pytest.param({"iss": "http://evil.test"}, id="wrong-issuer"),
        pytest.param({"sub": None}, id="no-subject"),
    ],
)
async def test_claims_are_required_and_checked(
    issuer: LocalIssuer, verifier: JwtVerifier, changes: dict[str, Any]
) -> None:
    assert await verifier.verify_token(signed(issuer, **changes)) is None


async def test_only_the_configured_keys_and_asymmetric_algorithms_count(
    issuer: LocalIssuer, verifier: JwtVerifier
) -> None:
    other = LocalIssuer.generate(issuer=ISSUER, audience=AUDIENCE)
    assert await verifier.verify_token(other.token("erin")) is None

    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "erin", "exp": int(time.time()) + 60}
    kid = {"kid": issuer.kid}
    unsigned = jwt.encode(claims, key="", algorithm="none", headers=kid)
    assert await verifier.verify_token(unsigned) is None

    hmac = jwt.encode(claims, "a-shared-secret-of-32-bytes-long", algorithm="HS256", headers=kid)
    assert await verifier.verify_token(hmac) is None

    assert await verifier.verify_token("not a jwt") is None


async def test_a_local_issuer_reloaded_from_its_key_signs_the_same_way(
    issuer: LocalIssuer, verifier: JwtVerifier
) -> None:
    reloaded = LocalIssuer.from_pem(issuer.pem(), issuer=ISSUER, audience=AUDIENCE)
    access = await verifier.verify_token(reloaded.token("erin"))
    assert access is not None and Caller.from_token(access).actor == "erin"
