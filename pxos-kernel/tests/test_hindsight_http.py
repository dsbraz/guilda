"""The Hindsight adapter speaks Hindsight's HTTP API (PXOS-53).

A fake server stands in for Hindsight, so CI checks the requests the adapter makes; the
real server is the opt-in contract test (test_context_contract).
"""

import json
from typing import Any

import httpx
import pytest

from pxos_kernel.context import ContextUnavailable, Recalled
from pxos_kernel.context.hindsight import HindsightContextStore

BANK = "/v1/default/banks/bank-a"


class FakeHindsight:
    """Records requests and answers each one with ``status`` and ``body``."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.body = {} if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def store(self, api_key: str | None = None) -> HindsightContextStore:
        return HindsightContextStore(
            "http://hindsight.test", "bank-a", api_key, transport=httpx.MockTransport(self)
        )


async def test_retaining_configures_the_bank_and_keys_the_document() -> None:
    server = FakeHindsight()
    await server.store("secret").retain(
        "fixture:doc", "texto", "Extraia promessas.", tags=["project:aurora"]
    )

    config, memories = server.requests
    assert (config.method, config.url.path) == ("PATCH", f"{BANK}/config")
    assert json.loads(config.content)["updates"] == {
        "retain_extraction_mode": "custom",
        "retain_custom_instructions": "Extraia promessas.",
        "enable_observations": False,
    }
    assert (memories.method, memories.url.path) == ("POST", f"{BANK}/memories")
    assert json.loads(memories.content) == {
        "items": [{"content": "texto", "document_id": "fixture:doc", "tags": ["project:aurora"]}],
        "async": False,
    }
    assert {r.headers["Authorization"] for r in server.requests} == {"Bearer secret"}


async def test_without_tags_the_document_is_retained_untagged() -> None:
    server = FakeHindsight()
    await server.store().retain("fixture:doc", "texto", "Extraia.")
    assert json.loads(server.requests[1].content)["items"][0]["tags"] == []


async def test_recall_is_scoped_to_the_given_tags_and_names_each_source() -> None:
    server = FakeHindsight(
        body={"results": [{"text": "Ana promete o diagnóstico.", "document_id": "fixture:doc"}]}
    )
    recalled = await server.store().recall("prazos", tags=["project:aurora", "project:boreal"])

    assert recalled == [Recalled(text="Ana promete o diagnóstico.", source="fixture:doc")]
    (request,) = server.requests
    assert (request.method, request.url.path) == ("POST", f"{BANK}/memories/recall")
    assert json.loads(request.content) == {
        "query": "prazos",
        "tags": ["project:aurora", "project:boreal"],
        "tags_match": "any_strict",
        "types": ["world", "experience"],
    }


async def test_recall_without_tags_reaches_nothing_and_asks_nothing() -> None:
    server = FakeHindsight()
    assert await server.store().recall("prazos", tags=[]) == []
    assert server.requests == []


async def test_a_refused_recall_raises() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await FakeHindsight(status=500).store().recall("prazos", tags=["project:aurora"])


async def test_a_recall_answer_without_results_raises() -> None:
    with pytest.raises(KeyError):
        await FakeHindsight(body={}).store().recall("prazos", tags=["project:aurora"])


async def test_forgetting_deletes_the_sources_document() -> None:
    server = FakeHindsight()
    await server.store().forget("sharepoint:sites/aurora/ata 1.docx")
    (request,) = server.requests
    assert request.method == "DELETE"
    assert request.url.raw_path.decode() == (
        f"{BANK}/documents/sharepoint%3Asites%2Faurora%2Fata%201.docx"
    )


async def test_forgetting_a_source_hindsight_does_not_have_is_harmless() -> None:
    await FakeHindsight(status=404).store().forget("fixture:doc")


async def test_a_refused_forget_raises() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await FakeHindsight(status=500).store().forget("fixture:doc")


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_a_network_failure_is_reported_as_context_unavailable(
    error: type[httpx.TransportError],
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise error("hindsight unreachable: Ana promete", request=request)

    store = HindsightContextStore(
        "http://hindsight.test", "bank-a", transport=httpx.MockTransport(fail)
    )
    with pytest.raises(ContextUnavailable) as raised:
        await store.recall("prazos", tags=["project:aurora"])
    assert "Ana" not in str(raised.value)


async def test_without_a_key_no_authorization_is_sent() -> None:
    server = FakeHindsight()
    await server.store().retain("fixture:doc", "texto", "Extraia.")
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
