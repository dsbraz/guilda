"""Isolated Mutmut campaign; the real database remains part of the selected tests."""

import fnmatch
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from quality.metrics import mutation_metrics
from quality.process import run


def _relative(path: str) -> str:
    return path.split("/src/")[-1]


def settings(policy: dict[str, Any]) -> dict[str, Any]:
    selected = [_relative(path) for paths in policy["mutation_areas"].values() for path in paths]
    return {
        "source_paths": ["pxos_kernel", "brq_at_work"],
        "only_mutate": [p if p.endswith(".py") else f"{p}/*" for p in selected],
        "do_not_mutate": policy["exclude"],
        "also_copy": [
            "pxos-kernel",
            "brq-at-work",
            "agents_testing.py",
            "db_testing.py",
            "temporal_testing.py",
        ],
        "pytest_add_cli_args_test_selection": policy["tests"][:2],
        "pytest_add_cli_args": ["-x", "-q"],
        "max_stack_depth": -1,
        "mutate_only_covered_lines": False,
        # Seconds added to each estimate, which leaves out the database fixtures; without
        # them a surviving mutant runs out of CPU time and is misread as a timeout.
        "timeout_constant": 10.0,
    }


def prepare(root: Path, target: Path, policy: dict[str, Any]) -> None:
    config = settings(policy)
    config["also_copy"] = [name for name in config["also_copy"] if (root / name).exists()]
    config["also_copy"] += [
        p.name for p in root.glob("*_testing.py") if p.name not in config["also_copy"]
    ]
    for name in config["also_copy"]:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, target / name)
    for name in policy["sources"][:2]:
        shutil.copytree(root / name, target / Path(name).name)
    text = (root / "pyproject.toml").read_text()
    text += "\n[tool.mutmut]\n"
    text += "\n".join(f"{key} = {json.dumps(value)}" for key, value in config.items())
    (target / "pyproject.toml").write_text(text + "\n")


def read_results(directory: Path, policy: dict[str, Any]) -> dict[str, Any]:
    collected: dict[str, list[str]] = {area: [] for area in policy["mutation_areas"]}
    # Pytest internal errors (3) are invalid, even though Mutmut calls them kills.
    statuses = {
        1: "killed",
        0: "survived",
        5: "no tests",
        33: "no tests",
        None: "not checked",
        36: "timeout",
        -24: "timeout",
        24: "timeout",
        152: "timeout",
        255: "timeout",
    }
    for meta in directory.rglob("*.py.meta"):
        name = meta.relative_to(directory).as_posix().removesuffix(".meta")
        results = json.loads(meta.read_text())["exit_code_by_key"]
        for area, paths in policy["mutation_areas"].items():
            patterns = [_relative(p) if p.endswith(".py") else f"{_relative(p)}/*" for p in paths]
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                collected[area].extend(statuses.get(code, "invalid") for code in results.values())
    return {area: mutation_metrics(values) for area, values in collected.items()}


def campaign(policy: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pxos-mutation-") as temporary:
        target = Path(temporary)
        prepare(root, target, policy)
        result = run(["mutmut", "run", "--max-children", "1"], output / "mutation.log", cwd=target)
        measured = read_results(target / "mutants", policy)
        for meta in (target / "mutants").rglob("*.meta"):
            destination = output / "mutants" / meta.relative_to(target / "mutants")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(meta, destination)
    result["metrics"] = measured
    result["passed"] = result["passed"] and all(
        row["complete"] and row["score"] >= policy["limits"]["mutation"]
        for row in measured.values()
    )
    return result
