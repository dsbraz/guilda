"""The Bot Connector REST API, called with the bot's own token.

The token comes from the bot's tenant (single-tenant bot) by client credentials, with
the bot's secret or a certificate assertion, and is reused until five minutes before it
expires. Calls go only to the service URL the deploy configures. A failed call raises,
so the outbox delivers the event again.
"""

import base64
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import jwt

from pxos_teams.settings import Settings

SCOPE = "https://api.botframework.com/.default"
ASSERTION = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
MARGIN = 300


class Connector:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (settings.secret is None) == (settings.certificate is None):
            raise ValueError("the bot needs either a secret or a certificate")
        self._settings = settings
        self._http = http
        self._clock = clock
        self._base = settings.service_url.rstrip("/") + "/v3/conversations"
        self._token_url = (
            f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token"
        )
        self._token: tuple[str, float] | None = None

    async def personal_chat(self, user: str) -> str:
        """The id of the bot's 1:1 chat with the Entra user ``user``; Teams returns the
        same chat every time."""
        tenant = self._settings.tenant_id
        created = await self._call(
            "POST",
            self._base,
            {
                "bot": {"id": f"28:{self._settings.app_id}"},
                "members": [{"id": user}],
                "tenantId": tenant,
                "isGroup": False,
                "channelData": {"tenant": {"id": tenant}},
            },
        )
        return str(created["id"])

    async def send(self, conversation: str, activity: dict[str, Any]) -> str:
        """Send ``activity`` to ``conversation``; return the new message's id."""
        sent = await self._call(
            "POST", f"{self._base}/{quote(conversation, safe='')}/activities", activity
        )
        return str(sent["id"])

    async def update(self, conversation: str, message: str, activity: dict[str, Any]) -> None:
        """Replace the message ``message`` of ``conversation`` with ``activity``."""
        url = f"{self._base}/{quote(conversation, safe='')}/activities/{quote(message, safe='')}"
        await self._call("PUT", url, activity)

    async def _call(self, method: str, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        response = await self._http.request(method, url, json=body, headers=headers)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def _access_token(self) -> str:
        now = self._clock()
        if self._token is not None and now < self._token[1]:
            return self._token[0]
        response = await self._http.post(self._token_url, data=self._credentials(now))
        response.raise_for_status()
        granted = response.json()
        self._token = (granted["access_token"], now + granted["expires_in"] - MARGIN)
        return self._token[0]

    def _credentials(self, now: float) -> dict[str, str]:
        settings = self._settings
        grant = {"grant_type": "client_credentials", "client_id": settings.app_id, "scope": SCOPE}
        if settings.certificate is None:
            assert settings.secret is not None
            return {**grant, "client_secret": settings.secret}
        thumbprint = bytes.fromhex(settings.certificate.thumbprint)
        assertion = jwt.encode(
            {
                "aud": self._token_url,
                "iss": settings.app_id,
                "sub": settings.app_id,
                "jti": str(uuid4()),
                "nbf": int(now),
                "exp": int(now) + 600,
            },
            settings.certificate.private_key_pem,
            algorithm="RS256",
            headers={"x5t": base64.urlsafe_b64encode(thumbprint).decode().rstrip("=")},
        )
        return {**grant, "client_assertion_type": ASSERTION, "client_assertion": assertion}
