"""A review card as Teams shows it: the report's fields as they come, the evidence, and
Approve or Reject, where only Reject needs (and sends) the reason."""

from typing import Any
from uuid import uuid4

from guilda_kernel.channel import Card
from guilda_teams.cards import adaptive_card

WEEKLY_REPORT = {
    "summary": "Semana estável, homologação em risco.",
    "items": [
        {"category": "progress", "text": "API entregue.", "sources": ["message:1", "doc:2"]},
        {"category": "delay_risk", "text": "Homologação pode atrasar.", "sources": ["doc:3"]},
    ],
    "uncertainties": ["Data da homologação."],
    "unavailable_sources": [],
}


def card() -> Card:
    return Card(
        proposal_id=uuid4(),
        recipient="ana",
        package="brq_weekly_report",
        action="propose_weekly_report",
        title="Report semanal",
        payload=WEEKLY_REPORT,
        quote="A API foi entregue na quarta.",
        source="agent-run:42",
        digest="d" * 64,
    )


def texts(content: dict[str, Any]) -> list[str]:
    return [block["text"] for block in content["body"] if block["type"] == "TextBlock"]


def test_the_card_shows_title_fields_items_with_sources_uncertainties_and_quote() -> None:
    content = adaptive_card(card())

    assert texts(content) == [
        "Report semanal",
        "summary",
        "Semana estável, homologação em risco.",
        "items",
        "- category: progress · text: API entregue. · sources: message:1, doc:2\n"
        "- category: delay_risk · text: Homologação pode atrasar. · sources: doc:3",
        "uncertainties",
        "- Data da homologação.",
        "unavailable_sources",
        "",
        "Evidência",
        "“A API foi entregue na quarta.”",
        "Fonte: agent-run:42",
    ]


def test_approve_and_reject_carry_the_proposal_and_digest_shown() -> None:
    shown = card()
    content = adaptive_card(shown)

    approve, reject = content["actions"]
    data = {"proposal_id": str(shown.proposal_id), "digest": shown.digest}
    assert (approve["type"], approve["verb"], approve["data"]) == (
        "Action.Execute",
        "approve",
        data,
    )
    assert (reject["type"], reject["verb"], reject["data"]) == ("Action.Execute", "reject", data)


def test_only_reject_requires_and_sends_the_reason() -> None:
    content = adaptive_card(card())

    [reason] = [block for block in content["body"] if block["type"] == "Input.Text"]
    assert (reason["id"], reason["isRequired"]) == ("reason", True)
    approve, reject = content["actions"]
    assert (approve["associatedInputs"], reject["associatedInputs"]) == ("none", "auto")
