"""Versioned evidence and comparisons against compatible approved main runs."""

import json
from pathlib import Path
from typing import Any


def write(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    name = report["mode"]
    (directory / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        f"# Quality {name}: {'PASS' if report['passed'] else 'FAIL'}",
        "",
        f"Commit: `{report['sha']}`",
        f"Policy: `{report['policy_hash']}`",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    lines += [
        f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in report["checks"].items()
    ]
    lines += ["", f"Full report: `{name}.json`", ""]
    (directory / f"{name}.md").write_text("\n".join(lines))


def history(directory: Path, current: dict[str, Any]) -> dict[str, Any]:
    reports = []
    for path in directory.rglob("all.json"):
        try:
            row = json.loads(path.read_text())
            reports.append(
                {key: value for key, value in row.items() if key not in {"history", "reports"}}
            )
        except (OSError, ValueError):
            continue
    reports.sort(key=lambda row: row.get("finished_at", ""), reverse=True)
    compatible = [
        row
        for row in reports
        if row.get("passed") is True
        and row.get("branch") == "main"
        and row.get("policy_hash") == current["policy_hash"]
    ]
    previous = compatible[0] if compatible else None
    return {
        "recent": reports[:10],
        "baseline": previous,
        "warning": None if previous else "No compatible approved main history available",
        "changes": differences(previous.get("metrics", {}), current.get("metrics", {}))
        if previous
        else {},
    }


def differences(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in current.items():
        old = previous.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            result[key] = differences(old, value)
        elif isinstance(value, (float, int)) and isinstance(old, (float, int)):
            result[key] = value - old
    return result
