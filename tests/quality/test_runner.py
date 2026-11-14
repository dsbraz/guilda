import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from quality import __main__ as runner
from quality.gates import REQUIRED
from quality.policy import load


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "quality.policy.json").write_text(json.dumps(load()))
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "areas", lambda policy: {"automation": [tmp_path / "a.py"]})
    monkeypatch.setattr(
        runner, "git", lambda command, root: "abc" if command[0] == "rev-parse" else "main"
    )
    monkeypatch.setattr(runner, "workspace", lambda root: [])
    monkeypatch.setattr(runner, "fingerprint", lambda root: "same")
    return tmp_path


def success(mode: str, *args: Any) -> dict[str, Any]:
    return {key: {"passed": True, "metrics": {"value": 100}} for key in REQUIRED[mode]}


def test_complete_reports_aggregate_only_for_same_clean_commit(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "perform", success)
    reports = [runner.execute(mode, repository) for mode in ["ci", "mutation"]]
    assert runner.aggregate(repository, reports)["passed"]
    monkeypatch.setattr(runner, "workspace", lambda root: ["M a.py"])
    assert not runner.aggregate(repository, reports)["passed"]


def test_cancelled_job_cannot_approve_existing_reports(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "perform", success)
    reports = [runner.execute(mode, repository) for mode in ["ci", "mutation"]]
    monkeypatch.setenv("PXOS_QUALITY_JOBS_STATUS", "cancelled")
    assert not runner.aggregate(repository, reports)["passed"]


@pytest.mark.parametrize("content", ["invalid", "[]", "null"])
def test_invalid_report_json_fails_without_losing_summary(repository: Path, content: str) -> None:
    (repository / "ci.json").write_text(content)
    assert runner.read_reports(repository) == [{}, {}]


def test_infrastructure_failure_writes_incomplete_report(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: Any) -> Any:
        raise OSError("unavailable")

    monkeypatch.setattr(runner, "perform", fail)
    result = runner.execute("ci", repository)
    assert not result["complete"]
    assert not result["passed"]
    assert "unavailable" in result["failures"][0]
    assert (repository / "ci.json").is_file()


def test_workspace_change_during_check_rejects_evidence(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter([[], ["M a.py"]])
    monkeypatch.setattr(runner, "workspace", lambda root: next(states))
    monkeypatch.setattr(runner, "perform", success)
    assert not runner.execute("ci", repository)["passed"]


def test_editing_already_dirty_file_invalidates_evidence(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashes = iter(["before", "after"])
    monkeypatch.setattr(runner, "fingerprint", lambda root: next(hashes))
    monkeypatch.setattr(runner, "perform", success)
    assert not runner.execute("ci", repository)["passed"]


def test_slow_ci_warns_but_does_not_relax_limits(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([0, 301])
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    monkeypatch.setattr(runner, "perform", success)
    assert "duration" in runner.execute("ci", repository)["warnings"][-1]


@pytest.mark.parametrize("mode", ["fast", "ci", "mutation"])
def test_modes_use_shared_checks(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "fast", lambda *args: {"fast": {"passed": True}})
    monkeypatch.setattr(runner, "suite", lambda *args: {"passed": False})
    monkeypatch.setattr(runner, "campaign", lambda *args: {"passed": False})
    monkeypatch.setattr(runner, "run", lambda *args, **kwargs: {"passed": True})
    result = runner.perform(mode, load(), tmp_path)
    assert ("suite" in result) == (mode == "ci")
    assert ("mutation" in result) == (mode == "mutation")


@pytest.mark.parametrize("mode,aggregate", [("fast", False), ("all", False), ("all", True)])
def test_cli_writes_summary_and_returns_gate_status(
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    aggregate: bool,
) -> None:
    monkeypatch.setattr(
        runner, "perform", lambda mode, *args: success("ci" if mode == "fast" else mode)
    )
    summary = repository / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        sys,
        "argv",
        ["quality", mode, "--output", str(repository)] + (["--aggregate"] if aggregate else []),
    )
    assert runner.main() == int(aggregate)
    assert summary.is_file()
