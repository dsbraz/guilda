"""The policy (PXOS-84, PRD §6.3): action × role × parameters × autonomy × module rules."""

from datetime import UTC, datetime

import pytest

from pxos_kernel.ontology import ActionType, Rule
from pxos_kernel.policy import Verdict, decide

WHEN = datetime(2026, 11, 14, 22, tzinfo=UTC)
READERS = ("store-staff", "store-manager")


def action(*rules: Rule, reversible: bool = True) -> ActionType:
    return ActionType(
        name="create_restock_order",
        creates="RestockOrder",
        approver="store-manager",
        reversible=reversible,
        rule_parameters=("quantity", "deliver_by"),
        rules=rules,
    )


QUANTITY = Rule(parameter="quantity", operator="max", value=10)
DELIVERY = Rule(parameter="deliver_by", operator="within_days", value=7)
PAYLOAD = {"sku": "A-1", "quantity": 3, "deliver_by": "2026-11-21"}


@pytest.mark.parametrize(
    ("declared", "autonomy", "payload", "roles", "expected"),
    [
        (action(QUANTITY), 2, PAYLOAD, {"store-staff"}, "allow"),
        (action(QUANTITY, DELIVERY), 2, PAYLOAD, {"store-staff"}, "allow"),
        (action(), 3, {**PAYLOAD, "quantity": 500}, {"store-staff"}, "allow"),
        (action(QUANTITY), 1, PAYLOAD, {"store-staff"}, "approval"),
        (action(), 2, PAYLOAD, {"store-staff"}, "approval"),
        (action(QUANTITY), 2, {**PAYLOAD, "quantity": 11}, {"store-staff"}, "approval"),
        (action(QUANTITY), 3, {**PAYLOAD, "quantity": 11}, {"store-staff"}, "approval"),
        (action(DELIVERY), 3, {**PAYLOAD, "deliver_by": "2026-11-22"}, {"store-staff"}, "approval"),
        (action(QUANTITY), 2, {"sku": "A-1"}, {"store-staff"}, "approval"),
        (action(QUANTITY, reversible=False), 3, PAYLOAD, {"store-staff"}, "approval"),
        (action(QUANTITY), 3, PAYLOAD, {"cashier"}, "deny"),
        (action(QUANTITY, reversible=False), 3, PAYLOAD, set(), "deny"),
    ],
    ids=[
        "phase-2-within-rule",
        "phase-2-within-both-rules",
        "phase-3-no-rules",
        "phase-1",
        "phase-2-no-rules",
        "phase-2-outside-rule",
        "phase-3-outside-rule",
        "phase-3-late-date",
        "missing-parameter",
        "irreversible",
        "principal-cannot-read",
        "deny-before-irreversible",
    ],
)
def test_the_policy_allows_asks_for_approval_or_denies(
    declared: ActionType,
    autonomy: int,
    payload: dict[str, object],
    roles: set[str],
    expected: str,
) -> None:
    verdict = decide(
        declared,
        readers=READERS,
        principal_roles=roles,
        autonomy=autonomy,
        payload=payload,
        valid_from=WHEN,
    )
    assert verdict.outcome == expected
    assert verdict.reason


def test_the_reason_names_what_decided() -> None:
    def reason(declared: ActionType, autonomy: int, payload: dict[str, object]) -> str:
        return decide(
            declared,
            readers=READERS,
            principal_roles={"store-staff"},
            autonomy=autonomy,
            payload=payload,
            valid_from=WHEN,
        ).reason

    assert reason(action(QUANTITY, reversible=False), 3, PAYLOAD) == "irreversible action"
    assert reason(action(QUANTITY), 1, PAYLOAD) == "autonomy phase 1"
    assert reason(action(), 2, PAYLOAD) == "phase 2 needs module rules"
    assert reason(action(QUANTITY), 2, {**PAYLOAD, "quantity": 11}) == "quantity above 10"
    assert reason(action(DELIVERY), 3, {**PAYLOAD, "deliver_by": "2026-12-01"}) == (
        "deliver_by beyond 7 days"
    )
    assert reason(action(QUANTITY), 2, PAYLOAD) == "within module rules in phase 2"
    assert Verdict("deny", "x").outcome == "deny"
