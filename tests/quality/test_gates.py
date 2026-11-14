from copy import deepcopy
from typing import Any

import pytest

from quality.gates import REQUIRED, approve, check_metrics


@pytest.mark.parametrize(
    "metric,limit",
    [
        ("function_lines", 60),
        ("file_lines", 300),
        ("complexity", 10),
        ("nesting", 3),
    ],
)
def test_upper_limits_are_inclusive(metric: str, limit: int) -> None:
    assert check_metrics({metric: limit}, {metric: limit}) == []
    assert check_metrics({metric: limit + 1}, {metric: limit})


@pytest.mark.parametrize(
    "metric,limit",
    [
        ("line_coverage", 90),
        ("branch_coverage", 90),
        ("mutation", 80),
    ],
)
def test_lower_limits_are_inclusive(metric: str, limit: int) -> None:
    assert check_metrics({metric: limit}, {metric: limit}) == []
    assert check_metrics({metric: limit - 0.01}, {metric: limit})


def report(mode: str) -> dict[str, Any]:
    return {
        "schema": 1,
        "mode": mode,
        "sha": "abc",
        "policy_hash": "def",
        "workspace": [],
        "complete": True,
        "passed": True,
        "checks": dict.fromkeys(REQUIRED[mode], True),
        "results": {key: {"passed": True} for key in REQUIRED[mode]},
        "scope": {"automation": ["quality/__init__.py"]},
        "tools": {"pytest": "9"},
        "policy_version": 1,
        "finished_at": "2026-11-14T00:00:00Z",
        "metrics": {"area": {}},
    }


def test_approval_requires_both_modes() -> None:
    assert approve([report("ci"), report("mutation")], "abc", "def") == []
    assert approve([report("ci")], "abc", "def")


@pytest.mark.parametrize(
    "field,value",
    [
        ("sha", "old"),
        ("policy_hash", "old"),
        ("workspace", ["M source.py"]),
        ("complete", False),
        ("passed", False),
        ("checks", {}),
        ("checks", None),
        ("results", []),
        ("results", {"suite": 1}),
        ("scope", []),
        ("schema", 0),
    ],
)
def test_invalid_evidence_cannot_approve(field: str, value: object) -> None:
    changed = deepcopy(report("ci"))
    changed[field] = value
    assert approve([changed, report("mutation")], "abc", "def")


def test_missing_report_fields_fail_closed() -> None:
    assert approve([{}, report("mutation")], "abc", "def")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_numeric_measurements_fail_closed(value: float) -> None:
    assert check_metrics({"line_coverage": value}, {"line_coverage": 90})
