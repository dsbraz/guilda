"""A review card as a Telegram message, and the button pressed back on it.

The adapter knows no package's schema, so it shows the payload as it comes: each field
under its name, a list one entry per line, an entry's own fields joined on that line. The
quote and its source follow. Text goes without ``parse_mode``, so nothing in the payload
is read as markup, and is cut to the 4096 characters Telegram takes. A button's
``callback_data`` takes at most 64 bytes, so it carries the decision, the proposal and
the start of the digest the card showed; the whole digest stays with the card's message.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from guilda_kernel.channel import Card
from guilda_kernel.state import Decision

LIMIT = 4096
VERBS: dict[str, Decision] = {"a": "approved", "r": "rejected"}
DIGEST_PREFIX = 16


def fit(text: str) -> str:
    """``text`` cut to what one Telegram message takes."""
    return text if len(text) <= LIMIT else text[: LIMIT - 1] + "…"


def card_text(card: Card) -> str:
    lines = [card.title, ""]
    for name, value in card.payload.items():
        lines += [f"{name}:", _block(value), ""]
    lines += ["Evidência:", f"“{card.quote}”", f"Fonte: {card.source}"]
    return fit("\n".join(lines))


def keyboard(card: Card) -> dict[str, Any]:
    reference = f"{card.proposal_id.hex}:{card.digest[:DIGEST_PREFIX]}"
    return {
        "inline_keyboard": [
            [
                {"text": "Aprovar", "callback_data": f"a:{reference}"},
                {"text": "Rejeitar", "callback_data": f"r:{reference}"},
            ]
        ]
    }


@dataclass(frozen=True)
class Callback:
    """What a button on a card asks for."""

    decision: Decision
    proposal_id: UUID
    digest: str
    """The start of the digest the card showed."""


class InvalidCallback(ValueError):
    """The button is not on a card of this adapter."""


def read_callback(data: Any) -> Callback:
    try:
        verb, proposal_id, digest = str(data).split(":")
        return Callback(VERBS[verb], UUID(hex=proposal_id), digest)
    except (KeyError, ValueError):
        raise InvalidCallback("not a review card button") from None


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
