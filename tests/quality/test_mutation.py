import json
from pathlib import Path
from typing import Any

import pytest

from quality import mutation
from quality.mutation import read_results, settings
from quality.policy import load


def test_mutation_configuration_includes_every_selected_responsibility() -> None:
    config = settings(load())
    assert config["max_stack_depth"] == -1
    assert config["mutate_only_covered_lines"] is False
    assert "pxos_kernel/state/*" in config["only_mutate"]
    assert "pxos_kernel/api/auth.py" in config["only_mutate"]


def test_mutation_missing_metadata_is_incomplete(tmp_path: Path) -> None:
    assert all(not row["complete"] for row in read_results(tmp_path, load()).values())


def test_mutation_reads_exit_states_without_counting_timeouts_as_kills(tmp_path: Path) -> None:
    directory = tmp_path / "pxos_kernel/state"
    directory.mkdir(parents=True)
    (directory / "canonical.py.meta").write_text(
        json.dumps(
            {
                "exit_code_by_key": {
                    "a": 1,
                    "b": 0,
                    "c": 36,
                    "d": 33,
                    "e": None,
                    "f": 3,
                }
            }
        )
    )
    result = read_results(tmp_path, load())["state"]
    assert result["killed"] == 1
    assert result["timeouts"] == result["uncovered"] == result["invalid"] == 1
    assert result["complete"] is False


def mutation_repository(root: Path) -> dict[str, Any]:
    for name in ["pxos-kernel/src/pxos_kernel", "brq-at-work/src/brq_at_work"]:
        source = root / name
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = []\n")
    (root / "extra_testing.py").write_text("# shared helper\n")
    return {**load(), "mutation_areas": {"state": ["pxos-kernel/src/pxos_kernel/state"]}}


@pytest.mark.parametrize("code,passed", [(1, True), (36, False), (None, False)])
def test_campaign_preserves_raw_results_and_rejects_incomplete_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int | None,
    passed: bool,
) -> None:
    policy = mutation_repository(tmp_path)
    output = tmp_path / "reports"
    output.mkdir()

    def run(command: list[str], log: Path, *, cwd: Path) -> dict[str, Any]:
        assert command == ["mutmut", "run", "--max-children", "1"]
        assert (cwd / "extra_testing.py").is_file()
        assert (cwd / "pxos_kernel/__init__.py").is_file()
        directory = cwd / "mutants/pxos_kernel/state"
        directory.mkdir(parents=True)
        (directory / "canonical.py.meta").write_text(
            json.dumps({"exit_code_by_key": {"mutant": code}})
        )
        return {"passed": True}

    monkeypatch.setattr(mutation, "run", run)
    result = mutation.campaign(policy, tmp_path, output)
    assert result["passed"] is passed
    assert (output / "mutants/pxos_kernel/state/canonical.py.meta").is_file()
