"""The weekly report is a canonical type of BRQ@work, reviewed by the PM (ADR 0006 §3).

Its categories tell a delay risk from a confirmed delay and a renegotiated deadline. Every
item carries its sources, and the report lists what it is unsure of and which sources it
could not reach. Only the project manager approves a report.
"""

from typing import Any

import pytest

from brq_at_work.composition import WEEKLY_REPORT
from pxos_kernel.ontology import InvalidPayload, Registry

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
