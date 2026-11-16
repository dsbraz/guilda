"""The weekly report is a canonical type of BRQ@work, reviewed by the PM (ADR 0006 §3).

Its categories tell a delay risk from a confirmed delay and a renegotiated deadline. Every
item carries its sources, and the report lists what it is unsure of and which sources it
could not reach. Only the project manager approves a report.
"""

import json
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from brq_at_work.composition import WEEKLY_REPORT
from brq_at_work.publish_agent import AGENT_FILE, publish
from db_testing import Database
from fakes_testing import FakeModels
from pxos_kernel.agents import (
    RoundInput,
    approved_version,
    build_agent,
    propose_deliverable,
    run_round,
)
from pxos_kernel.identity import session
from pxos_kernel.model_access import ModelGateway
from pxos_kernel.ontology import InvalidPayload, Registry
from pxos_kernel.state import InvalidProposal, review

AGENT = "weekly-report-agent"

REPORT: dict[str, Any] = {
    "summary": "Semana de homologação.",
    "items": [
        {"category": "delay_risk", "text": "A API pode atrasar.", "sources": ["ata:1"]},
        {"category": "renegotiated_deadline", "text": "Go-live em 12/12.", "sources": ["e:2"]},
    ],
    "uncertainties": ["Não se sabe se o cliente aprovou o escopo."],
    "unavailable_sources": ["sharepoint:cronograma"],
}


def test_the_pm_approves_the_report_and_the_agent_reads_it(registry: Registry) -> None:
    package = registry.get(WEEKLY_REPORT).package
    action = package.action("propose_weekly_report")
    assert (action.creates, action.approver) == ("WeeklyReport", "project-manager")
    assert set(package.readers["WeeklyReport"]) == {"project-manager", "agent"}


def test_a_report_keeps_its_categories_and_sources(registry: Registry) -> None:
    assert registry.get(WEEKLY_REPORT).validate("WeeklyReport", REPORT) == REPORT


@pytest.mark.parametrize(
    "item",
    [
        {"category": "rumor", "text": "x", "sources": ["s"]},
        {"category": "progress", "text": "x", "sources": []},
        {"category": "progress", "text": "x"},
    ],
)
def test_an_item_needs_a_known_category_and_a_source(registry: Registry, item: Any) -> None:
    with pytest.raises(InvalidPayload):
        registry.get(WEEKLY_REPORT).validate("WeeklyReport", {**REPORT, "items": [item]})


async def no_context(query: str) -> list[tuple[str, str]]:
    return []


async def test_a_round_of_the_agent_proposes_the_report_to_the_pm(
    database: Database, pool: asyncpg.Pool, registry: Registry
) -> None:
    draft = await publish(database.app_dsn, AGENT_FILE, author="bruno")
    async with session(pool, "eva") as connection:
        await review(connection, registry, draft.id, "approved", digest=draft.digest)
    models = FakeModels(reply=json.dumps({"questions": None, "deliverable": REPORT}))
    async with session(pool, AGENT) as connection:
        version = await approved_version(connection, AGENT)
        model = await ModelGateway(pool, models).adk_model()
        agent = await build_agent(connection, model, registry, version, recall=no_context)

    cycle = f"cycle:{uuid4()}"
    unavailable = RoundInput(unavailable_sources=("sharepoint:cronograma",))
    output = await run_round(pool, agent, unavailable, cycle=cycle, round=1)
    assert output.deliverable == REPORT
    proposal = await propose_deliverable(pool, registry, version, cycle=cycle, round=1)

    async with session(pool, "bruno") as connection:
        with pytest.raises(InvalidProposal):  # a team member does not even see the report
            await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
    async with session(pool, "ana") as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
