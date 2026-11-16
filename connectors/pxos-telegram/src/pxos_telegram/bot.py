"""The Bot API, called over httpx with the bot's token in the URL, as Telegram requires.

So the token stays out of everything else: a failed call raises ``TelegramError`` with
the method and the HTTP status or the exception's type alone, never the request or its
cause, and the ``httpx`` logger, which writes each request's URL, gets the token masked.
A failed call raises, so the outbox delivers the event again.
"""

import logging
import re
from typing import Any

import httpx

API = "https://api.telegram.org"
TOKEN_IN_URL = re.compile(r"/bot[^/\s]+/")


class TelegramError(Exception):
    """A Bot API call failed."""


class _MaskToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/bot" in message:
            record.msg, record.args = TOKEN_IN_URL.sub("/bot<token>/", message), ()
        return True


_MASK = _MaskToken()


class BotApi:
    def __init__(self, token: str, http: httpx.AsyncClient) -> None:
        self._base = f"{API}/bot{token}/"
        self._http = http
        logging.getLogger("httpx").addFilter(_MASK)  # adding it again is a no-op

    async def call(self, method: str, *, wait: float | None = None, **params: Any) -> Any:
        """The ``result`` of ``method`` with ``params`` (``None`` left out); ``wait`` is
        the HTTP timeout, for a long poll."""
        body = {name: value for name, value in params.items() if value is not None}
        timeout = httpx.USE_CLIENT_DEFAULT if wait is None else wait
        try:
            response = await self._http.post(self._base + method, json=body, timeout=timeout)
        except httpx.HTTPError as error:
            raise TelegramError(f"{method} failed: {type(error).__name__}") from None
        try:
            reply = response.json()
        except ValueError:
            reply = None
        if not response.is_success or not isinstance(reply, dict) or not reply.get("ok"):
            raise TelegramError(f"{method} failed: HTTP {response.status_code}")
        return reply["result"]
