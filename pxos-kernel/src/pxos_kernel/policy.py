"""The policy (PXOS-84, PXOS-87, PRD §6.3): allow, ask for approval or deny an agent's call.

The kernel provides the mechanism and the rule types; the business rules belong to the
distribution, which declares them at build time, and the deploy may replace them per
action. A rule checks one condition and says what happens when it fails:

- ``max``: a number in the payload is at most ``value``;
- ``within_days``: a date in the payload is at most ``value`` days after the start of
  validity;
- ``roles``: whom the agent acts for holds one of ``roles``.

What stays in the kernel because the PRD requires it: an irreversible action always goes
to a person; phase 1 asks for approval; phase 2 acts directly only on actions with rules,
within them (the rules define "low risk"); phase 3 also on actions without rules, and
declared rules still bind. The database refuses on its own a direct execution outside
these rules.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import asyncpg
import yaml

from pxos_kernel.errors import PxosError
from pxos_kernel.identity import Connection
from pxos_kernel.ontology import ActionType, InvalidPackage, Registry

Outcome = Literal["allow", "approval", "deny"]
Kind = Literal["max", "within_days", "roles"]
Effect = Literal["approval", "deny"]
Policy = dict[tuple[str, str], tuple["Rule", ...]]
RANGES = {"max": ("integer", "float", "decimal"), "within_days": ("date", "datetime")}
KEYS = {"parameter", "otherwise", "max", "within_days", "roles"}


class InvalidPolicy(PxosError):
    """A policy file breaks the rule types or does not fit the registered packages."""


@dataclass(frozen=True)
class Rule:
    kind: Kind
    effect: Effect
    parameter: str | None = None
    value: float | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str


def decide(
    action: ActionType,
    rules: Iterable[Rule],
    *,
    principal_roles: Collection[str],
    autonomy: int,
    payload: Mapping[str, object],
    valid_from: datetime,
) -> Verdict:
    rules = tuple(rules)
    broken = [(rule, _broken(rule, payload, principal_roles, valid_from)) for rule in rules]
    for rule, reason in broken:
        if reason and rule.effect == "deny":
            return Verdict("deny", reason)
    if not action.reversible:
        return Verdict("approval", "irreversible action")
    if autonomy < 2:
        return Verdict("approval", f"autonomy phase {autonomy}")
    if autonomy == 2 and not rules:
        return Verdict("approval", "phase 2 needs rules")
    for _, reason in broken:
        if reason:
            return Verdict("approval", reason)
    return Verdict("allow", f"within rules in phase {autonomy}")


def read_policy(paths: Iterable[Path], registry: Registry) -> Policy:
    """Rules per (package, action); each later file replaces the actions it declares."""
    policy: Policy = {}
    for path in paths:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, items in (content.get("actions") or {}).items():
            package, _, action = str(key).partition(".")
            declared = _action(registry, package, action)
            policy[(package, action)] = tuple(
                _rule(registry, package, declared, item) for item in items or []
            )
    return policy


async def install_policy(owner: asyncpg.Connection, policy: Policy) -> None:
    """Replace the installation's rules, as the owner; the application only reads them."""
    async with owner.transaction():
        await owner.execute("DELETE FROM pxos.policy_rules")
        await owner.executemany(
            """
            INSERT INTO pxos.policy_rules
                (package, action, position, kind, effect, parameter, value, roles)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            [
                (package, action, position, r.kind, r.effect, r.parameter, r.value, list(r.roles))
                for (package, action), rules in policy.items()
                for position, r in enumerate(rules)
            ],
        )


async def installed_rules(connection: Connection, package: str, action: str) -> tuple[Rule, ...]:
    rows = await connection.fetch(
        "SELECT kind, effect, parameter, value, roles FROM pxos.policy_rules"
        " WHERE package = $1 AND action = $2 ORDER BY position",
        package,
        action,
    )
    return tuple(
        Rule(
            kind=row["kind"],
            effect=row["effect"],
            parameter=row["parameter"],
            value=None if row["value"] is None else float(row["value"]),
            roles=tuple(row["roles"] or ()),
        )
        for row in rows
    )


def _action(registry: Registry, package: str, action: str) -> ActionType:
    try:
        return registry.get(package).package.action(action)
    except InvalidPackage:
        raise InvalidPolicy(
            f"{package}.{action} is not an action of a registered package"
        ) from None


def _rule(registry: Registry, package: str, declared: ActionType, item: dict[str, Any]) -> Rule:
    where = f"{package}.{declared.name}"
    unknown = sorted(set(item) - KEYS)
    if unknown:
        raise InvalidPolicy(f"{where}: unknown keys {unknown}")
    effect = item.get("otherwise")
    if effect not in ("approval", "deny"):
        raise InvalidPolicy(f"{where}: otherwise must be approval or deny")
    kinds = [kind for kind in ("max", "within_days", "roles") if kind in item]
    if len(kinds) != 1:
        raise InvalidPolicy(f"{where}: a rule needs exactly one of max, within_days, roles")
    if kinds[0] == "roles":
        return _roles_rule(where, item, effect)
    kind = kinds[0]
    parameter = item.get("parameter")
    if parameter not in declared.rule_parameters:
        raise InvalidPolicy(f"{where}: {parameter} is not a rule parameter")
    view = registry.get(package).package.schema
    slot_range = view.induced_slot(parameter, declared.creates).range
    if slot_range not in RANGES[kind]:
        expected = "a number" if kind == "max" else "a date"
        raise InvalidPolicy(f"{where}: {kind} needs {expected}, not {slot_range}")
    value = item[kind]
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise InvalidPolicy(f"{where}: value must be a non-negative number")
    return Rule(kind=kind, effect=effect, parameter=parameter, value=value)


def _roles_rule(where: str, item: dict[str, Any], effect: Effect) -> Rule:
    roles = item["roles"]
    if "parameter" in item:
        raise InvalidPolicy(f"{where}: roles takes no parameter")
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles):
        raise InvalidPolicy(f"{where}: roles must be a non-empty list")
    return Rule(kind="roles", effect=effect, roles=tuple(roles))


def _broken(
    rule: Rule, payload: Mapping[str, object], roles: Collection[str], valid_from: datetime
) -> str | None:
    """Why the call breaks the rule, or None when it respects it."""
    if rule.kind == "roles":
        if set(rule.roles) & set(roles):
            return None
        return f"requires role {' or '.join(rule.roles)}"
    value = payload.get(rule.parameter or "")
    limit = rule.value or 0
    if rule.kind == "max":
        if isinstance(value, int | float) and not isinstance(value, bool) and value <= limit:
            return None
        return f"{rule.parameter} above {limit:g}"
    deadline = valid_from.astimezone(UTC).date() + timedelta(days=int(limit))
    if isinstance(value, str) and date.fromisoformat(value[:10]) <= deadline:
        return None
    return f"{rule.parameter} beyond {limit:g} days"
