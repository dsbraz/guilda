"""The pilot's weekly agent, run by the kernel with fakes for SharePoint, Teams and the
model: what the scenarios of the MVP share, in this process or in a worker subprocess.

The scripted model behaves as the version's instructions ask, as a function of what it is
given only, so a worker started again after a crash answers the same way. Each round it
first recalls the project's context, then either asks the thread or delivers the report.

    python pilot.py DSN TEMPORAL_ADDRESS TASK_QUEUE

runs a worker of the agent's cycle until it is killed.
"""

import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import asyncpg
from google.adk.models.llm_request import LlmRequest
from google.genai import types
from temporalio.client import Client
from temporalio.worker import Worker

from brq_at_work.composition import ACCESS, build_registry
from fakes_testing import FakeChannel, FakeConnector, FakeContextStore, FakeModels
from guilda_kernel.channel import Channel
from guilda_kernel.context import ContextStore
from guilda_kernel.ingestion import SourceDocument
from guilda_kernel.model_access import ModelGateway
from guilda_kernel.ontology import Registry
from guilda_kernel.state import create_pool
from guilda_kernel.workflows import AgentCycle, CycleActivities, workflow_runner

PROJECT = ACCESS[0]  # the version's recall tag
QUESTION = "A entrega de sexta saiu?"
TOKENS = (1_000, 100)
"""What each model call of a round reports; a round recalls, then answers: two calls."""

Script = Callable[[dict[str, Any], list[dict[str, str]]], dict[str, Any]]
"""What the agent answers, from the round's data and what recall returned."""


def model(script: Script) -> FakeModels:
    def reply(request: LlmRequest) -> str | types.Part:
        found = [
            part.function_response.response or {}
            for content in request.contents
            for part in content.parts or []
            if part.function_response
        ]
        if not found:
            call = types.FunctionCall(name="recall", args={"query": "marco 2 do projeto"})
            return types.Part(function_call=call)
        data = json.loads(str(request.contents[0].parts[0].text))  # type: ignore[index]
        return json.dumps(script(data, found[0]["results"]))

    return FakeModels(reply=reply, tokens=TOKENS)


def refs(texts: list[str], kind: str) -> list[str]:
    """The ``kind:<id>`` references the round's answers or corrections came with."""
    return [m for text in texts for m in re.findall(rf"\(({kind}:[^)]+)\)", text)]


def report(*items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    deliverable = {"summary": "Semana do projeto piloto.", "items": list(items), **extra}
    return {"questions": None, "deliverable": deliverable}


def minutes(recalled: list[dict[str, str]]) -> list[str]:
    return [r["source"] for r in recalled if r["source"].startswith("sharepoint:")]


def weekly(data: dict[str, Any], recalled: list[dict[str, str]]) -> dict[str, Any]:
    """Asks whether Friday's delivery happened, and reports what the team answered, with
    the corrections of the round; unanswered in the last round, it says so."""
    answered = refs(data["answers"], "message")
    if not answered and not data["final_round"]:
        return {"questions": [QUESTION], "deliverable": None}
    sources = answered + refs(data["corrections"], "rejection") or minutes(recalled)
    text = data["answers"][-1].rsplit("): ", 1)[1] if answered else "sem resposta do time."
    item = {"category": "confirmed_delay", "text": f"Entrega de sexta: {text}", "sources": sources}
    return report(item, unavailable_sources=data["unavailable_sources"])


def continuing(data: dict[str, Any], recalled: list[dict[str, str]]) -> dict[str, Any]:
    """Reads milestone 2 as delayed from the minutes, unless a correction, in the round or
    recalled from an earlier cycle, says its deadline was renegotiated."""
    corrections = [(text, ref) for text in data["corrections"] for ref in refs([text], "rejection")]
    corrections += [
        (r["text"], r["source"]) for r in recalled if r["source"].startswith("rejection:")
    ]
    fixes = [ref for text, ref in corrections if "renegociado" in text]
    if fixes:
        text = "O prazo do marco 2 foi renegociado para 30/11."
        return report({"category": "renegotiated_deadline", "text": text, "sources": fixes[:1]})
    item = {
        "category": "confirmed_delay",
        "text": "O marco 2 atrasou.",
        "sources": minutes(recalled),
    }
    return report(item)


def reviewing(data: dict[str, Any], recalled: list[dict[str, str]]) -> dict[str, Any]:
    """Reports milestone 2 as a risk, with what it could not confirm, until the PM corrects
    it to a confirmed delay."""
    corrected = refs(data["corrections"], "rejection")
    category, text = (
        ("confirmed_delay", "O marco 2 atrasou.")
        if corrected
        else (
            "delay_risk",
            "O marco 2 pode atrasar.",
        )
    )
    item = {"category": category, "text": text, "sources": minutes(recalled) + corrected}
    return report(item, uncertainties=["O cliente ainda não confirmou o novo escopo."])


def connector() -> FakeConnector:
    """SharePoint for the pilot: the minutes in the project's scope, and a board note that
    is not; both say milestone 2 slipped. Each worker gets its own sources, ingested once."""
    sharepoint, copy = FakeConnector(f"sharepoint-{uuid4()}"), uuid4()
    for name, access in (("ata-2026-11-10", PROJECT), ("diretoria", "board:restricted")):
        sharepoint.put(
            SourceDocument(
                source=f"sharepoint:piloto/{name}/{copy}",
                version="1",
                text=f"{name}: o marco 2 atrasou.",
                access=(access,),
            )
        )
    return sharepoint


@asynccontextmanager
async def cycle_worker(
    temporal: Client,
    pool: asyncpg.Pool,
    registry: Registry,
    script: Script,
    *,
    queue: str,
    store: ContextStore,
    channel: Channel,
    **options: Any,
) -> AsyncIterator[None]:
    steps = CycleActivities(
        pool, store, [connector()], ModelGateway(pool, model(script)), registry, channel
    )
    async with Worker(
        temporal,
        task_queue=queue,
        workflows=[AgentCycle],
        activities=steps.registered(),
        workflow_runner=workflow_runner(),
        **options,
    ):
        yield


async def main(dsn: str, address: str, queue: str) -> None:
    pool = await create_pool(dsn)
    temporal = await Client.connect(address)
    store, channel = FakeContextStore(), FakeChannel()
    # No cache: a task after a crash goes to the next worker at once.
    async with cycle_worker(
        temporal,
        pool,
        build_registry(),
        weekly,
        queue=queue,
        store=store,
        channel=channel,
        max_cached_workflows=0,
    ):
        print("ready", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
