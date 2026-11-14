import json
import sys
from pathlib import Path
from typing import Any

import pytest

from quality import checks, process
from quality.policy import load


def test_command_failure_and_missing_executable_are_evidence(tmp_path: Path) -> None:
    result = process.run(
        [sys.executable, "-c", "print('failed'); exit(7)"], tmp_path / "failure.log", cwd=tmp_path
    )
    assert result["returncode"] == 7
    assert result["passed"] is False
    assert "failed" in (tmp_path / "failure.log").read_text()
    missing = process.run(["/no/such/executable"], tmp_path / "missing.log", cwd=tmp_path)
    assert missing["passed"] is False
    assert missing["returncode"] == -1


def test_fingerprint_detects_changes_in_untracked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        process, "git", lambda cmd, root: "new.py" if cmd[0] == "ls-files" else "diff"
    )
    file = tmp_path / "new.py"
    file.write_text("one")
    before = process.fingerprint(tmp_path)
    file.write_text("two")
    assert process.fingerprint(tmp_path) != before


def test_size_gate_reports_exact_file_and_function(tmp_path: Path) -> None:
    folder = tmp_path / "quality"
    folder.mkdir()
    file = folder / "large.py"
    file.write_text("def large():\n" + "    x = 1\n" * 60 + "    return x\n")
    policy = {**load(), "sources": ["quality"]}
    measured, failures = checks.sizes(policy, tmp_path)
    assert measured["quality/large.py"]["lines"] == 62
    assert any("large.py:large:1" in failure for failure in failures)
    file.write_text("x = 1\n" * 301)
    assert "file_lines" in checks.sizes(policy, tmp_path)[1][0]


def fixture_repository(root: Path) -> dict[str, Any]:
    for name in ["kernel", "distribution", "quality"]:
        (root / name).mkdir()
        (root / name / "__init__.py").write_text("")
    return {**load(), "sources": ["kernel", "distribution", "quality"]}


def test_fast_runs_all_commands_even_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = fixture_repository(tmp_path)
    commands = []

    def run(command: list[str], *args: Any, **kwargs: Any) -> dict[str, Any]:
        commands.append(command)
        return {"passed": command[0] != "ruff"}

    monkeypatch.setattr(checks, "run", run)
    results = checks.fast(policy, tmp_path, tmp_path)
    assert len(commands) == 4
    assert results["architecture"]["passed"]
    assert not results["lint"]["passed"]
    assert results["size"]["passed"]


@pytest.mark.parametrize("missing", [False, True])
def test_suite_requires_coverage_and_test_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    policy = fixture_repository(tmp_path)

    def run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if not missing:
            summary = {
                "covered_lines": 8,
                "num_statements": 10,
                "covered_branches": 9,
                "num_branches": 10,
            }
            files = {
                f"{name}/__init__.py": {"summary": summary}
                for name in ["kernel", "distribution", "quality"]
            }
            (tmp_path / "coverage.json").write_text(json.dumps({"files": files}))
            (tmp_path / "outcomes.json").write_text(json.dumps({"exitstatus": 0, "tests": []}))
        return {"passed": True}

    monkeypatch.setattr(checks, "run", run)
    result = checks.suite(policy, tmp_path, tmp_path)
    assert not result["passed"]
    assert result["failures"]
