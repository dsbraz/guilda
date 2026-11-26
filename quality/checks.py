"""Local and CI checks share the same policy and invocations."""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from quality.configuration import architecture_config, coverage_config, ruff_args
from quality.gates import check_metrics
from quality.metrics import coverage_metrics, size_metrics
from quality.outcomes import check_outcomes
from quality.policy import areas
from quality.process import run
from quality.structure import measure


def sizes(policy: dict[str, Any], root: Path) -> tuple[dict[str, Any], list[str]]:
    measured = {}
    failures = []
    for files in areas(policy, root).values():
        for file in files:
            name = file.relative_to(root).as_posix()
            value = size_metrics(file.read_text())
            measured[name] = value
            failures += [
                f"{name}: {failure}"
                for failure in check_metrics({"file_lines": value["lines"]}, policy["limits"])
            ]
            for function, count in value["functions"].items():
                failures += [
                    f"{name}:{function}: {failure}"
                    for failure in check_metrics({"function_lines": count}, policy["limits"])
                ]
    return measured, failures


def fast(policy: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    config = output / "imports.ini"
    config.write_text(
        architecture_config(
            [root / p for p in [*policy["sources"][:2], *policy.get("adapters", [])]]
        )
    )
    commands = {
        "lint": ["ruff", "check", *ruff_args(policy), *policy["lint"]],
        "format": ["ruff", "format", "--check", *policy["lint"]],
        "types": ["mypy"],
        "architecture": ["lint-imports", "--config", str(config), "--no-cache"],
    }
    results = {name: run(cmd, output / f"{name}.log", cwd=root) for name, cmd in commands.items()}
    structure = measure(policy, root, output)
    results["lint"]["metrics"] = structure.get("metrics", {})
    results["lint"]["passed"] = results["lint"]["passed"] and structure["passed"]
    measured, failures = sizes(policy, root)
    results["size"] = {"passed": not failures, "metrics": measured, "failures": failures}
    return results


def suite(policy: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    config = output / "coverage.ini"
    config.write_text(coverage_config(policy))
    coverage = output / "coverage.json"
    outcomes = output / "outcomes.json"
    result = run(
        [
            sys.executable,
            "-m",
            "pytest",
            *policy["tests"],
            "-p",
            "quality.outcomes",
            "--cov",
            "--cov-branch",
            f"--cov-config={config}",
            f"--cov-report=json:{coverage}",
            "--cov-report=term-missing",
        ],
        output / "tests.log",
        cwd=root,
        env={"GUILDA_QUALITY_OUTCOMES": str(outcomes)},
    )
    failures = []
    measured: dict[str, Any] = {}
    try:
        measured = coverage_metrics(json.loads(coverage.read_text()), areas(policy, root), root)
        for area, values in measured.items():
            failures += [
                f"{area}: {failure}" for failure in check_metrics(values, policy["limits"])
            ]
        results = json.loads(outcomes.read_text())
        failures += check_outcomes(results["tests"], policy)
        result["outcomes"] = results
        result["test_counts"] = dict(Counter(row["outcome"] for row in results["tests"]))
    except (OSError, ValueError, KeyError) as error:
        failures.append(str(error))
    result.update(metrics=measured, failures=failures, passed=result["passed"] and not failures)
    return result
