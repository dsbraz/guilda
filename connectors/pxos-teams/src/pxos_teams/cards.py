"""A review card as an Adaptive Card, and the reviewer's click back from it.

The adapter knows no package's schema, so it shows the payload as it comes: each field
under its name, a list one entry per line, an entry's own fields joined on that line. The
quote and its source follow. Approve sends nothing else; Reject sends a reason, which the
card requires before sending. Both actions carry the proposal and the digest the card
showed, so the decision is on exactly what the reviewer saw.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pxos_kernel.channel import Card
from pxos_kernel.state import Decision

CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
VERBS: dict[str, Decision] = {"approve": "approved", "reject": "rejected"}


def card_activity(card: Card) -> dict[str, Any]:
    """The message that shows ``card``."""
    return {
        "type": "message",
        "attachments": [{"contentType": CONTENT_TYPE, "content": adaptive_card(card)}],
    }


def adaptive_card(card: Card) -> dict[str, Any]:
    data = {"proposal_id": str(card.proposal_id), "digest": card.digest}
    body: list[dict[str, Any]] = [_text(card.title, size="Large", weight="Bolder")]
    for name, value in card.payload.items():
        body += [_text(name, weight="Bolder"), _text(_block(value))]
    body += [
        _text("Evidência", weight="Bolder"),
        _text(f"“{card.quote}”", isSubtle=True),
        _text(f"Fonte: {card.source}", isSubtle=True, size="Small"),
        {
            "type": "Input.Text",
            "id": "reason",
            "label": "Motivo da rejeição",
            "isMultiline": True,
            "isRequired": True,
            "errorMessage": "Informe o motivo para rejeitar.",
        },
    ]
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
        "actions": [
            _action("approve", "Aprovar", data, associatedInputs="none"),
            _action("reject", "Rejeitar", data, associatedInputs="auto"),
        ],
    }


@dataclass(frozen=True)
class Click:
    """What a reviewer's click on a card asks for."""

    proposal_id: UUID
    digest: str
    decision: Decision
    reason: str | None


class InvalidClick(ValueError):
    """The click does not come from a card of this adapter."""


class MissingReason(InvalidClick):
    """A rejection without its reason."""


def read_click(value: Any) -> Click:
    """The click in an ``adaptiveCard/action`` invoke's ``value``."""
    try:
        action = value["action"]
        data = action["data"]
        decision = VERBS[action["verb"]]
        proposal_id, digest = UUID(data["proposal_id"]), str(data["digest"])
        reason = str(data.get("reason") or "").strip() or None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InvalidClick("not a review card click") from None
    if decision == "approved":
        return Click(proposal_id, digest, decision, None)
    if reason is None:
        raise MissingReason("a rejection needs its reason")
    return Click(proposal_id, digest, decision, reason)


def _text(text: str, **style: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **style}


def _action(verb: str, title: str, data: dict[str, str], **options: str) -> dict[str, Any]:
    return {"type": "Action.Execute", "verb": verb, "title": title, "data": data, **options}


def _block(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {_inline(entry)}" for entry in value)
    return _inline(value)


def _inline(value: Any) -> str:
    if isinstance(value, dict):
        return " · ".join(f"{key}: {_inline(entry)}" for key, entry in value.items())
    if isinstance(value, list):
        return ", ".join(_inline(entry) for entry in value)
    return str(value)
