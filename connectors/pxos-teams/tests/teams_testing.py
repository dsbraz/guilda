"""Microsoft's side of the bot, faked over httpx: the Bot Framework OpenID metadata and
signing keys (locally generated RSA keys), the Entra token endpoint and the Bot Connector.
Tokens are signed as the Bot Connector signs them, so the adapter validates real JWTs."""

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from pxos_teams import Settings

APP_ID = "00000000-0000-0000-0000-00000000b07a"
TENANT = "11111111-1111-1111-1111-111111111111"
SERVICE_URL = "https://smba.example.test/teams/"
CHANNEL = "19:project@thread.tacv2"
THREAD = "projeto-piloto"
SETTINGS = Settings(
    app_id=APP_ID,
    tenant_id=TENANT,
    service_url=SERVICE_URL,
    threads={THREAD: CHANNEL},
    secret="s3cret",
)
"""Each person's Entra object id; the identities bind ``teams:<id>`` to the actor."""
AAD = {name: f"aad-{name}" for name in ["carla", "diego", "erin", "vera", "frank"]}


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class Signer:
    """A signing key as the Bot Connector publishes it, with its endorsements."""

    kid: str = field(default_factory=lambda: str(uuid4()))
    key: rsa.RSAPrivateKey = field(default_factory=new_key)
    endorsements: tuple[str, ...] = ("msteams",)

    def jwk(self) -> dict[str, Any]:
        public: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**public, "kid": self.kid, "use": "sig", "endorsements": list(self.endorsements)}

    def token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": "https://api.botframework.com",
            "aud": APP_ID,
            "nbf": now - 10,
            "exp": now + 3600,
            "serviceurl": SERVICE_URL,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})


class Microsoft:
    """httpx transport handler for every Microsoft endpoint the adapter calls."""

    def __init__(self) -> None:
        self.signers = [Signer()]
        self.requests: list[httpx.Request] = []
        self.conversations: dict[str, list[dict[str, Any]]] = {}
        self.fail: int | None = None

    @property
    def signer(self) -> Signer:
        return self.signers[0]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def sent(self, method: str, fragment: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and fragment in str(r.url)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url == "https://login.botframework.com/v1/.well-known/openidconfiguration":
            return httpx.Response(200, json={"jwks_uri": "https://keys.example.test/keys"})
        if url == "https://keys.example.test/keys":
            return httpx.Response(200, json={"keys": [s.jwk() for s in self.signers]})
        if url.startswith("https://login.microsoftonline.com/"):
            return httpx.Response(200, json={"access_token": f"tok-{uuid4()}", "expires_in": 3599})
        if self.fail is not None:
            return httpx.Response(self.fail, json={"error": "nope"})
        return self.connector(request)

    def connector(self, request: httpx.Request) -> httpx.Response:
        """``/v3/conversations[/{conversation}/activities[/{activity}]]``, ids quoted."""
        path = request.url.raw_path.decode().removeprefix("/teams/v3/conversations")
        parts = [unquote(part) for part in path.split("/")[1:]]
        body = json.loads(request.content)
        if not parts:
            conversation = f"a:{body['members'][0]['id']}"
            self.conversations.setdefault(conversation, [])
            return httpx.Response(201, json={"id": conversation})
        activities = self.conversations.setdefault(parts[0], [])
        if len(parts) == 2:
            activities.append({**body, "id": f"act-{len(activities)}"})
            return httpx.Response(201, json={"id": activities[-1]["id"]})
        index = int(parts[2].removeprefix("act-"))
        activities[index] = {**body, "id": parts[2]}
        return httpx.Response(200, json={"id": parts[2]})


def activity(**overrides: Any) -> dict[str, Any]:
    """A Teams message in the project channel's reply chain, as the connector posts it."""
    base: dict[str, Any] = {
        "type": "message",
        "id": f"1700000{uuid4().int % 1000000}",
        "channelId": "msteams",
        "serviceUrl": SERVICE_URL,
        "from": {"id": "29:1abc", "name": "Erin", "aadObjectId": AAD["erin"]},
        "conversation": {
            "conversationType": "channel",
            "id": f"{CHANNEL};messageid=1700000000001",
            "tenantId": TENANT,
        },
        "recipient": {"id": f"28:{APP_ID}", "name": "pxOS"},
        "text": "<at>pxOS</at> Cinco caixas chegam na terça.",
        "entities": [{"type": "mention", "text": "<at>pxOS</at>"}],
        "channelData": {"tenant": {"id": TENANT}, "channel": {"id": CHANNEL}},
    }
    base.update(overrides)
    return base
