"""The policy (PXOS-84, PRD §6.3): allow, ask for approval or deny an agent's action call.

A pure function of the action, the roles of whom the agent acts for, the parameters, the
agent's autonomy phase and the module's rules. The database refuses on its own whatever
this function must never allow; the decision here picks the path and its audited reason.

- Deny when the principal cannot read the type the action creates.
- An irreversible action always goes to a person.
- Phase 1 asks for approval; phase 2 acts directly only on actions whose module declares
  rules; phase 3 also on actions without rules. Declared rules always bind.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pxos_kernel.ontology import ActionType, Rule

Outcome = Literal["allow", "approval", "deny"]


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str


def decide(
    action: ActionType,
    *,
    readers: Collection[str],
    principal_roles: Collection[str],
    autonomy: int,
    payload: Mapping[str, object],
    valid_from: datetime,
) -> Verdict:
    if not set(readers) & set(principal_roles):
        return Verdict("deny", f"principal cannot read {action.creates}")
    if not action.reversible:
        return Verdict("approval", "irreversible action")
    if autonomy < 2:
        return Verdict("approval", f"autonomy phase {autonomy}")
    if autonomy == 2 and not action.rules:
        return Verdict("approval", "phase 2 needs module rules")
    for rule in action.rules:
        broken = _broken(rule, payload.get(rule.parameter), valid_from)
        if broken:
            return Verdict("approval", broken)
    return Verdict("allow", f"within module rules in phase {autonomy}")


def _broken(rule: Rule, value: object, valid_from: datetime) -> str | None:
    """Why the value breaks the rule, or None when it respects it."""
    if rule.operator == "max":
        if isinstance(value, int | float) and value <= rule.value:
            return None
        return f"{rule.parameter} above {rule.value:g}"
    limit = valid_from.astimezone(UTC).date() + timedelta(days=int(rule.value))
    if isinstance(value, str) and date.fromisoformat(value[:10]) <= limit:
        return None
    return f"{rule.parameter} beyond {rule.value:g} days"
