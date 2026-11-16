"""Telegram's Bot API, faked over httpx: it keeps every call and answers as Telegram does,
with a new message id for each message sent and the queued updates for ``getUpdates``."""

import asyncio
import json
from typing import Any

import httpx

from pxos_telegram import Settings

TOKEN = "123456:SECRET-token_value"
CHAT = -1001234567890
TOPIC = 7
THREAD = "projeto-piloto"
SETTINGS = Settings(bot_token=TOKEN, chat_id=CHAT, thread=THREAD)
"""Each person's Telegram user id; the identities bind ``telegram:<id>`` to the actor."""
USERS = {"carla": 1001, "diego": 1002, "erin": 1003, "vera": 1004, "frank": 1005}


class Telegram:
    """httpx transport handler for ``https://api.telegram.org/bot<token>/<method>``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[list[dict[str, Any]]] = []
        self.fail: int | None = None
        self.refused: set[str] = set()
        """Methods Telegram answers with 400."""
        self._next_id = 100

    def client(self) -> httpx.AsyncClient:
        async def handle(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0)  # a real call yields to the loop
            return self.handle(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))

    @property
    def last_id(self) -> int:
        """The id of the last message sent or edited."""
        return self._next_id

    def sent(self, method: str) -> list[dict[str, Any]]:
        return [params for name, params in self.calls if name == method]

    def handle(self, request: httpx.Request) -> httpx.Response:
        prefix = f"/bot{TOKEN}/"
        assert request.url.host == "api.telegram.org" and request.url.path.startswith(prefix)
        method = request.url.path.removeprefix(prefix)
        params = json.loads(request.content)
        self.calls.append((method, params))
        if method in self.refused:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})
        if self.fail is not None:
            return httpx.Response(self.fail, json={"ok": False, "description": "Forbidden"})
        return httpx.Response(200, json={"ok": True, "result": self.result(method)})

    def result(self, method: str) -> Any:
        if method == "getUpdates":
            return self.updates.pop(0) if self.updates else []
        if method in {"sendMessage", "editMessageText"}:
            self._next_id += 1
            return {"message_id": self._next_id}
        return True


def message(user: str = "erin", chat: int = CHAT, **overrides: Any) -> dict[str, Any]:
    """A message from ``user`` in ``chat``, as ``getUpdates`` brings it."""
    base: dict[str, Any] = {
        "message_id": 5000 + USERS.get(user, 0),
        "from": {"id": USERS.get(user, 9999), "is_bot": False, "first_name": user},
        "chat": {"id": chat, "type": "supergroup" if chat < 0 else "private"},
        "date": 1700000000,
        "text": "Cinco caixas chegam na terça.",
    }
    base.update(overrides)
    return base
