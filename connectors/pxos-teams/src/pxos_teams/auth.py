"""Validation of the Bot Connector's calls to the bot.

Every call carries a JWT the Bot Connector signs. It counts only as a Bearer token signed
with RS256 by a key in the Bot Framework OpenID keys, endorsed for the activity's channel,
issued by ``https://api.botframework.com`` to the bot's app id, within its validity (five
minutes of clock skew) and carrying the activity's ``serviceUrl``. The algorithm is fixed,
never read from the token. Keys are cached for a day; a key id not in the cache refetches
them, at most once every five minutes, so made-up key ids cannot make the bot hammer
Microsoft. There is no way to turn validation off, and no Emulator path.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

OPENID = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
SKEW = 300
KEYS_TTL = 24 * 3600
REFETCH_INTERVAL = 300


class Unauthorized(Exception):
    """The call does not come from the Bot Connector for this bot and this activity."""


class Authenticator:
    def __init__(
        self,
        http: httpx.AsyncClient,
        app_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._app_id = app_id
        self._clock = clock
        self._keys: dict[str, tuple[jwt.PyJWK, tuple[str, ...]]] = {}
        self._fetched_at: float | None = None

    async def verify(self, authorization: str | None, activity: dict[str, Any]) -> None:
        """Raise Unauthorized unless ``authorization`` is the Bot Connector's token for
        ``activity``."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not token:
            raise Unauthorized("no bearer token")
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key, endorsements = await self._key(str(kid))
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._app_id,
                issuer=ISSUER,
                leeway=SKEW,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            raise Unauthorized(type(error).__name__) from None
        if activity.get("channelId") not in endorsements:
            raise Unauthorized("key not endorsed for the channel")
        service_url = claims.get("serviceurl", claims.get("serviceUrl"))
        if not service_url or service_url != activity.get("serviceUrl"):
            raise Unauthorized("serviceUrl does not match")

    async def _key(self, kid: str) -> tuple[jwt.PyJWK, tuple[str, ...]]:
        now = self._clock()
        age = None if self._fetched_at is None else now - self._fetched_at
        if age is None or age > KEYS_TTL or (kid not in self._keys and age > REFETCH_INTERVAL):
            await self._fetch(now)
        if kid not in self._keys:
            raise jwt.InvalidKeyError("unknown key id")
        return self._keys[kid]

    async def _fetch(self, now: float) -> None:
        metadata = await self._http.get(OPENID)
        metadata.raise_for_status()
        keys = await self._http.get(metadata.json()["jwks_uri"])
        keys.raise_for_status()
        self._keys = {
            item["kid"]: (jwt.PyJWK(item, algorithm="RS256"), tuple(item.get("endorsements", ())))
            for item in keys.json()["keys"]
            if item.get("kty") == "RSA"
        }
        self._fetched_at = now
