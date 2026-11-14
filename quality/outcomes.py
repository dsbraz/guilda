"""Pytest evidence: external skips and pending requirements are not delivered features."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

_rows: list[dict[str, Any]] = []


def check_outcomes(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    failures = [] if rows else ["no test outcomes recorded"]
    for row in rows:
        file = row["nodeid"].split("::")[0]
        if row["outcome"] == "skipped":
            allowed = policy["external_skips"].get(file)
            if not allowed or allowed not in row.get("reason", ""):
                failures.append(f"unexpected skip: {row['nodeid']}")
        if row["outcome"] == "xfailed":
            if file != policy["pending_requirements"] or row.get("strict") is not True:
                failures.append(f"unexpected/non-strict xfail: {row['nodeid']}")
    return failures


def pytest_sessionstart(session: Any) -> None:
    _rows.clear()
    if session.config.getoption("reruns", default=0):
        raise pytest.UsageError("quality policy forbids automatic test retries")


def pytest_collection_modifyitems(items: list[Any]) -> None:
    for item in items:
        if item.get_closest_marker("flaky"):
            raise pytest.UsageError(f"quality policy forbids retries: {item.nodeid}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Any:
    result = yield
    report = result.get_result()
    if report.when == "call" or report.skipped or report.failed:
        marker = item.get_closest_marker("xfail")
        _rows.append(
            {
                "nodeid": report.nodeid,
                "outcome": "xfailed" if hasattr(report, "wasxfail") else report.outcome,
                "reason": str(report.longrepr or ""),
                "strict": bool(marker and marker.kwargs.get("strict")),
            }
        )


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    destination = os.environ.get("PXOS_QUALITY_OUTCOMES")
    if destination:
        Path(destination).write_text(json.dumps({"exitstatus": exitstatus, "tests": _rows}))
