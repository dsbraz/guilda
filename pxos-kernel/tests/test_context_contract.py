"""Context port contract (PXOS-53): one suite for every adapter, so the fake cannot drift.

The Hindsight adapter runs only when PXOS_HINDSIGHT_URL points to a running Hindsight
(local, opt-in: it calls a real model). CI runs the fake.
"""

import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

import pxos_kernel
from fakes_testing import FakeContextStore
from pxos_kernel.context import ContextStore

TEXT = "Erin: I will restock aisle 3 with 12 boxes of A-1 tomorrow morning.\nCarla: thanks.\n"
INSTRUCTIONS = "Extract every restock promise, with who promised it and the verbatim sentence."


ADAPTERS = [
    pytest.param("fake", id="fake"),
    pytest.param(
        "hindsight",
        id="hindsight",
        marks=pytest.mark.skipif(
            not os.environ.get("PXOS_HINDSIGHT_URL"), reason="PXOS_HINDSIGHT_URL not set"
        ),
    ),
]


def store_for(kind: str, key: str) -> ContextStore:
    if kind == "fake":
        return FakeContextStore()
    from pxos_kernel.context.hindsight import HindsightContextStore

    return HindsightContextStore.from_environment()


@pytest.fixture
def key() -> str:
    return f"fixture:contract/{uuid4()}"


@pytest.mark.parametrize("kind", ADAPTERS)
async def test_retaining_the_same_document_twice_is_harmless(kind: str, key: str) -> None:
    store = store_for(kind, key)
    await store.retain(key, TEXT, INSTRUCTIONS)
    await store.retain(key, TEXT, INSTRUCTIONS)


@pytest.mark.parametrize("kind", ADAPTERS)
async def test_recall_returns_only_what_the_tags_reach(kind: str, key: str) -> None:
    store = store_for(kind, key)
    allowed, restricted = f"access:{uuid4()}", f"access:{uuid4()}"
    await store.retain(key, TEXT, INSTRUCTIONS, tags=[restricted])

    assert await store.recall("Who will restock aisle 3?", tags=[allowed]) == []
    assert await store.recall("Who will restock aisle 3?", tags=[]) == []
    recalled = await store.recall("Who will restock aisle 3?", tags=[allowed, restricted])
    assert recalled and {r.source for r in recalled} == {key}


@pytest.mark.parametrize("kind", ADAPTERS)
async def test_a_forgotten_source_is_never_recalled(kind: str, key: str) -> None:
    store = store_for(kind, key)
    tag = f"access:{uuid4()}"
    await store.retain(key, TEXT, INSTRUCTIONS, tags=[tag])

    await store.forget(key)
    await store.forget(key)

    assert await store.recall("Who will restock aisle 3?", tags=[tag]) == []


def test_only_the_adapter_talks_to_hindsight() -> None:
    source = Path(pxos_kernel.__file__).parent
    importers = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+(httpx|hindsight_client)\b", path.read_text(), re.M)
    }
    assert importers == {"context/hindsight.py"}
