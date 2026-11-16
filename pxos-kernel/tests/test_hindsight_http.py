"""The Hindsight adapter speaks Hindsight's HTTP API (PXOS-53).

A fake server stands in for Hindsight, so CI checks the requests the adapter makes; the
real server is the opt-in contract test (test_context_contract).
"""

import json

import httpx
import pytest

from pxos_kernel.context.hindsight import HindsightContextStore

BANK = "/v1/default/banks/bank-a"


class FakeHindsight:
    """Records requests and answers each one with success."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={})

    def store(self, api_key: str | None = None) -> HindsightContextStore:
        return HindsightContextStore(
            "http://hindsight.test", "bank-a", api_key, transport=httpx.MockTransport(self)
        )


async def test_retaining_configures_the_bank_and_keys_the_document() -> None:
    server = FakeHindsight()
    await server.store("secret").retain("fixture:doc@1", "texto", "Extraia promessas.")

    config, memories = server.requests
    assert (config.method, config.url.path) == ("PATCH", f"{BANK}/config")
    assert json.loads(config.content)["updates"] == {
        "retain_extraction_mode": "custom",
        "retain_custom_instructions": "Extraia promessas.",
        "enable_observations": False,
    }
    assert (memories.method, memories.url.path) == ("POST", f"{BANK}/memories")
    assert json.loads(memories.content) == {
        "items": [{"content": "texto", "document_id": "fixture:doc@1"}],
        "async": False,
    }
    assert {r.headers["Authorization"] for r in server.requests} == {"Bearer secret"}


async def test_without_a_key_no_authorization_is_sent() -> None:
    server = FakeHindsight()
    await server.store().retain("fixture:doc@1", "texto", "Extraia.")
    assert all("Authorization" not in r.headers for r in server.requests)


async def test_a_refused_retain_raises() -> None:
    store = HindsightContextStore(
        "http://hindsight.test",
        "bank-a",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await store.retain("fixture:doc@1", "texto", "Extraia.")


def test_the_store_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PXOS_HINDSIGHT_URL", "http://hindsight.test")
    monkeypatch.delenv("PXOS_HINDSIGHT_BANK", raising=False)
    monkeypatch.setenv("PXOS_HINDSIGHT_API_KEY", "secret")
    store = HindsightContextStore.from_environment()
    assert str(store._client.base_url) == "http://hindsight.test"
    assert store._bank == "/v1/default/banks/pxos-local"
    assert store._client.headers["Authorization"] == "Bearer secret"
