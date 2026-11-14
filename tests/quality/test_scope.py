from pathlib import Path

import pytest

from quality.policy import areas, digest, excluded, load


def test_areas_discover_new_subpackages_and_unimported_files(tmp_path: Path) -> None:
    source = tmp_path / "src/pxos_kernel/new"
    source.mkdir(parents=True)
    (source / "never.py").write_text("x = 1")
    (source / "generated.py").write_text("x = 1")
    policy = {**load(), "sources": ["src/pxos_kernel"]}
    assert areas(policy, tmp_path) == {"new": [source / "never.py"]}


def test_missing_source_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing source"):
        areas(load(), tmp_path)


def test_policy_hash_is_order_independent_and_captures_limits() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})


def test_exclusions_preserve_manual_sources() -> None:
    assert excluded("pxos-kernel/src/pxos_kernel/state/migrations/env.py", load())
    assert not excluded("pxos-kernel/src/pxos_kernel/state/db.py", load())
