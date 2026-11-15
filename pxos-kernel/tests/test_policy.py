"""The policy (PXOS-84, PXOS-87, PRD §6.3).

The kernel provides the rule types and the decision; the distribution declares the rules
at build time and the deploy may replace them per action.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from pxos_kernel.ontology import ActionType, Registry
from pxos_kernel.policy import InvalidPolicy, Rule, Verdict, decide, read_policy

WHEN = datetime(2026, 11, 14, 22, tzinfo=UTC)
RETAIL_POLICY = Path(__file__).parent / "retail_fixture" / "policy.yaml"


def action(reversible: bool = True) -> ActionType:
    return ActionType(
        name="create_restock_order",
        creates="RestockOrder",
        approver="store-manager",
        reversible=reversible,
        rule_parameters=("quantity", "deliver_by"),
    )


QUANTITY = Rule(kind="max", effect="approval", parameter="quantity", value=10)
DELIVERY = Rule(kind="within_days", effect="approval", parameter="deliver_by", value=7)
STAFF = Rule(kind="roles", effect="deny", roles=("store-staff", "store-manager"))
CEILING = Rule(kind="max", effect="deny", parameter="quantity", value=100)
PAYLOAD = {"sku": "A-1", "quantity": 3, "deliver_by": "2026-11-21"}


def verdict(
    rules: tuple[Rule, ...],
    autonomy: int,
    payload: dict[str, object] = PAYLOAD,
    roles: frozenset[str] = frozenset({"store-staff"}),
    reversible: bool = True,
) -> Verdict:
    return decide(
        action(reversible),
        rules,
        principal_roles=roles,
        autonomy=autonomy,
        payload=payload,
        valid_from=WHEN,
    )


@pytest.mark.parametrize(
    ("rules", "autonomy", "payload", "roles", "reversible", "expected"),
    [
        ((QUANTITY,), 2, PAYLOAD, {"store-staff"}, True, ("allow", "within rules in phase 2")),
        (
            (QUANTITY, DELIVERY),
            2,
            PAYLOAD,
            {"store-staff"},
            True,
            ("allow", "within rules in phase 2"),
        ),
        (
            (),
            3,
            {**PAYLOAD, "quantity": 500},
            {"store-staff"},
            True,
            ("allow", "within rules in phase 3"),
        ),
        ((QUANTITY,), 1, PAYLOAD, {"store-staff"}, True, ("approval", "autonomy phase 1")),
        ((), 2, PAYLOAD, {"store-staff"}, True, ("approval", "phase 2 needs rules")),
        (
            (QUANTITY,),
            2,
            {**PAYLOAD, "quantity": 11},
            {"store-staff"},
            True,
            ("approval", "quantity above 10"),
        ),
        (
            (QUANTITY,),
            3,
            {**PAYLOAD, "quantity": 11},
            {"store-staff"},
            True,
            ("approval", "quantity above 10"),
        ),
        (
            (DELIVERY,),
            3,
            {**PAYLOAD, "deliver_by": "2026-11-22"},
            {"store-staff"},
            True,
            ("approval", "deliver_by beyond 7 days"),
        ),
        ((QUANTITY,), 2, {"sku": "A-1"}, {"store-staff"}, True, ("approval", "quantity above 10")),
        ((QUANTITY,), 3, PAYLOAD, {"store-staff"}, False, ("approval", "irreversible action")),
        (
            (STAFF,),
            3,
            PAYLOAD,
            {"cashier"},
            True,
            ("deny", "requires role store-staff or store-manager"),
        ),
        (
            (STAFF,),
            3,
            PAYLOAD,
            set(),
            False,
            ("deny", "requires role store-staff or store-manager"),
        ),
        (
            (CEILING,),
            1,
            {**PAYLOAD, "quantity": 101},
            {"store-staff"},
            True,
            ("deny", "quantity above 100"),
        ),
        (
            (STAFF, QUANTITY),
            2,
            PAYLOAD,
            {"store-staff"},
            True,
            ("allow", "within rules in phase 2"),
        ),
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
        "role-denied",
        "deny-before-irreversible",
        "ceiling-denies-in-any-phase",
        "deny-rule-also-counts-as-rule",
    ],
)
def test_the_policy_allows_asks_for_approval_or_denies(
    rules: tuple[Rule, ...],
    autonomy: int,
    payload: dict[str, object],
    roles: set[str],
    reversible: bool,
    expected: tuple[str, str],
) -> None:
    decided = verdict(rules, autonomy, payload, frozenset(roles), reversible)
    assert (decided.outcome, decided.reason) == expected


def test_the_distribution_declares_rules_and_the_deploy_replaces_them(
    registry: Registry, tmp_path: Path
) -> None:
    policy = read_policy([RETAIL_POLICY], registry)
    assert policy[("retail", "create_restock_order")] == (
        STAFF,
        QUANTITY,
    )

    deploy = tmp_path / "deploy.yaml"
    deploy.write_text(
        "actions:\n"
        "  retail.create_restock_order:\n"
        "    - {parameter: quantity, max: 5, otherwise: approval}\n"
        "  retail.confirm_restock_order: []\n",
        encoding="utf-8",
    )
    replaced = read_policy([RETAIL_POLICY, deploy], registry)
    assert replaced[("retail", "create_restock_order")] == (
        Rule(kind="max", effect="approval", parameter="quantity", value=5),
    )
    assert replaced[("retail", "confirm_restock_order")] == ()


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ({"retail.unknown": []}, "retail.unknown is not an action"),
        ({"nowhere.create_restock_order": []}, "nowhere.create_restock_order is not an action"),
        (
            {"retail.create_restock_order": [{"parameter": "sku", "max": 1, "otherwise": "deny"}]},
            "sku is not a rule parameter",
        ),
        (
            {"retail.create_restock_order": [{"parameter": "quantity", "otherwise": "deny"}]},
            "exactly one of max, within_days, roles",
        ),
        (
            {
                "retail.create_restock_order": [
                    {"parameter": "quantity", "within_days": 2, "otherwise": "deny"}
                ]
            },
            "within_days needs a date",
        ),
        (
            {"retail.create_restock_order": [{"parameter": "quantity", "max": 1}]},
            "otherwise must be approval or deny",
        ),
        (
            {
                "retail.create_restock_order": [
                    {"parameter": "quantity", "max": -1, "otherwise": "deny"}
                ]
            },
            "value must be a non-negative number",
        ),
        (
            {"retail.create_restock_order": [{"roles": [], "otherwise": "deny"}]},
            "roles must be a non-empty list",
        ),
        (
            {
                "retail.create_restock_order": [
                    {"roles": ["store-staff"], "parameter": "quantity", "otherwise": "deny"}
                ]
            },
            "roles takes no parameter",
        ),
        (
            {
                "retail.create_restock_order": [
                    {"parameter": "quantity", "max": 1, "otherwise": "deny", "limit": 2}
                ]
            },
            "unknown keys \\['limit'\\]",
        ),
    ],
)
def test_an_invalid_policy_is_refused(
    registry: Registry, tmp_path: Path, rules: dict[str, object], message: str
) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump({"actions": rules}), encoding="utf-8")
    with pytest.raises(InvalidPolicy, match=message):
        read_policy([path], registry)


def test_max_needs_a_number_and_within_days_a_date(registry: Registry, tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "actions:\n  retail.create_restock_order:\n"
        "    - {parameter: occurred_at, max: 1, otherwise: deny}\n",
        encoding="utf-8",
    )
    with pytest.raises(InvalidPolicy, match="max needs a number"):
        read_policy([path], registry)
