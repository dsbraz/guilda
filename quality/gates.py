"""Fail-closed decisions, separate from measurement and process execution."""

import math
from typing import Any

REQUIRED = {
    "ci": {"lint", "format", "types", "architecture", "size", "generation", "suite"},
    "mutation": {"mutation"},
}


def check_metrics(values: dict[str, float], limits: dict[str, float]) -> list[str]:
    lower = {"line_coverage", "branch_coverage", "mutation"}
    failures = []
    for name, value in values.items():
        limit = limits[name]
        if not math.isfinite(value) or value < 0:
            failures.append(f"{name}: invalid measurement {value}")
            continue
        failed = value < limit if name in lower else value > limit
        if failed:
            failures.append(f"{name}: {value:g} (limit {limit:g})")
    return failures


def approve(reports: list[dict[str, Any]], sha: str, policy_hash: str) -> list[str]:
    failures = []
    modes = [report.get("mode") for report in reports]
    if sorted(str(mode) for mode in modes) != ["ci", "mutation"]:
        failures.append("exactly one ci and one mutation report required")
    for report in reports:
        expected = {
            "schema": 1,
            "sha": sha,
            "policy_hash": policy_hash,
            "workspace": [],
            "complete": True,
            "passed": True,
        }
        for key, value in expected.items():
            if report.get(key) != value:
                failures.append(f"{report.get('mode')}: invalid {key}")
        checks = report.get("checks")
        checks = checks if isinstance(checks, dict) else {}
        required = REQUIRED.get(str(report.get("mode")), set())
        if not required or set(checks) != required or not all(v is True for v in checks.values()):
            failures.append("missing or failed checks")
        evidence = ["scope", "tools", "policy_version", "finished_at", "metrics", "results"]
        if any(not report.get(key) for key in evidence):
            failures.append("incomplete report evidence")
        results = report.get("results")
        results = results if isinstance(results, dict) else {}
        if set(results) != required or any(
            not isinstance(row, dict) or row.get("passed") is not True for row in results.values()
        ):
            failures.append("missing or failed check results")
    return failures
