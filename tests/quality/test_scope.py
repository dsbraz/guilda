from pathlib import Path

import pytest

from quality.policy import areas, digest, excluded, load


def test_areas_discover_new_subpackages_and_unimported_files(tmp_path: Path) -> None:
    source = tmp_path / "src/guilda_kernel/new"
    source.mkdir(parents=True)
    (source / "never.py").write_text("x = 1")
    (source / "generated.py").write_text("x = 1")
    policy = {**load(), "sources": ["src/guilda_kernel"]}
    assert areas(policy, tmp_path) == {"new": [source / "never.py"]}


def test_missing_source_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing source"):
        areas(load(), tmp_path)


def test_policy_hash_is_order_independent_and_captures_limits() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_exclusions_preserve_manual_sources() -> None:
    assert excluded("guilda-kernel/src/guilda_kernel/state/migrations/env.py", load())
    assert not excluded("guilda-kernel/src/guilda_kernel/state/db.py", load())


def test_each_adapter_is_its_own_area(tmp_path: Path) -> None:
    source = tmp_path / "connectors/guilda-granola/src/guilda_granola"
    source.mkdir(parents=True)
    (source / "notes.py").write_text("x = 1")
    adapter = "connectors/guilda-granola/src/guilda_granola"
    policy = {**load(), "sources": [adapter], "adapters": [adapter]}
    assert areas(policy, tmp_path) == {"guilda_granola": [source / "notes.py"]}
