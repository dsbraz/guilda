"""Run with ``uv run python -m quality fast|ci|mutation|all``."""

import argparse
import importlib.metadata
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quality.checks import fast, suite
from quality.gates import approve
from quality.mutation import campaign
from quality.policy import ROOT, areas, digest, load
from quality.process import fingerprint, git, run, workspace
from quality.reports import history, write


def execute(mode: str, output: Path) -> dict[str, Any]:
    policy = load()
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema": 1,
        "mode": mode,
        "sha": git(["rev-parse", "HEAD"], ROOT),
        "branch": os.environ.get("GITHUB_REF_NAME") or git(["branch", "--show-current"], ROOT),
        "workspace": workspace(ROOT),
        "workspace_hash": fingerprint(ROOT),
        "policy_version": policy["version"],
        "policy_hash": digest(policy),
        "scope": {
            key: [str(p.relative_to(ROOT)) for p in paths] for key, paths in areas(policy).items()
        },
        "tools": {
            name: importlib.metadata.version(name)
            for name in ["ruff", "mypy", "coverage", "pytest", "mutmut", "import-linter"]
        },
        "complete": False,
        "passed": False,
        "checks": {},
        "results": {},
        "failures": [],
    }
    try:
        report["results"] = perform(mode, policy, output)
        report["checks"] = {key: value["passed"] for key, value in report["results"].items()}
        report["complete"] = True
        report["passed"] = bool(report["checks"]) and all(report["checks"].values())
    except (Exception, KeyboardInterrupt) as error:
        report["failures"].append(f"{type(error).__name__}: {error}")
    report["seconds"] = time.monotonic() - started
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["metrics"] = {key: value.get("metrics", {}) for key, value in report["results"].items()}
    report["metrics"]["test_counts"] = report["results"].get("suite", {}).get("test_counts", {})
    report["history"] = history(ROOT / "quality-history", report)
    report["warnings"] = [report["history"]["warning"]] if report["history"]["warning"] else []
    if mode == "ci" and report["seconds"] > policy["limits"]["ci_warning_seconds"]:
        report["warnings"].append("CI duration exceeds policy warning threshold")
    if (
        workspace(ROOT) != report["workspace"]
        or fingerprint(ROOT) != report["workspace_hash"]
        or git(["rev-parse", "HEAD"], ROOT) != report["sha"]
    ):
        report["passed"] = False
        report["failures"].append("workspace changed during validation")
    write(output, report)
    return report


def perform(mode: str, policy: dict[str, Any], output: Path) -> dict[str, Any]:
    if mode == "mutation":
        return {"mutation": campaign(policy, ROOT, output)}
    results = fast(policy, ROOT, output)
    if mode == "ci":
        results["generation"] = run(
            [
                sys.executable,
                "-m",
                "pxos_kernel.ontology.generate",
                "--check",
                *policy["generation"],
            ],
            output / "generation.log",
            cwd=ROOT,
        )
        results["suite"] = suite(policy, ROOT, output)
    return results


def aggregate(output: Path, reports: list[dict[str, Any]]) -> dict[str, Any]:
    policy = load()
    sha = git(["rev-parse", "HEAD"], ROOT)
    failures = approve(reports, sha, digest(policy))
    if workspace(ROOT):
        failures.append("final evidence requires a clean workspace")
    if os.environ.get("PXOS_QUALITY_JOBS_STATUS", "success") != "success":
        failures.append("required CI jobs did not finish successfully")
    report = {
        "schema": 1,
        "mode": "all",
        "sha": sha,
        "policy_hash": digest(policy),
        "policy_version": policy["version"],
        "workspace": workspace(ROOT),
        "branch": os.environ.get("GITHUB_REF_NAME") or git(["branch", "--show-current"], ROOT),
        "finished_at": datetime.now(UTC).isoformat(),
        "complete": not failures,
        "passed": not failures,
        "checks": {str(r.get("mode")): r.get("passed", False) for r in reports},
        "failures": failures,
        "reports": reports,
        "metrics": {str(r.get("mode")): r.get("metrics", {}) for r in reports},
    }
    report["history"] = history(ROOT / "quality-history", report)
    write(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["fast", "ci", "mutation", "all"])
    parser.add_argument("--output", type=Path, default=ROOT / "quality-reports")
    parser.add_argument(
        "--aggregate", action="store_true", help="Verify existing ci/mutation reports"
    )
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if arguments.mode == "all":
        if arguments.aggregate:
            reports = read_reports(output)
        else:
            reports = [execute(mode, output) for mode in ["ci", "mutation"]]
        result = aggregate(output, reports)
    else:
        result = execute(arguments.mode, output)
    print(f"{arguments.mode}: {'PASS' if result['passed'] else 'FAIL'} — {output}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as file:
            file.write((output / f"{arguments.mode}.md").read_text())
    return 0 if result["passed"] else 1


def read_reports(output: Path) -> list[dict[str, Any]]:
    reports = []
    for mode in ["ci", "mutation"]:
        try:
            value = json.loads((output / f"{mode}.json").read_text())
            reports.append(value if isinstance(value, dict) else {})
        except (OSError, ValueError):
            reports.append({})
    return reports


if __name__ == "__main__":
    sys.exit(main())
