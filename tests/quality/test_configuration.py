import os
import subprocess
from pathlib import Path

from quality.configuration import architecture_config, ruff_args
from quality.policy import load


def test_ruff_rejects_complexity_and_nesting(tmp_path: Path) -> None:
    source = tmp_path / "bad.py"
    source.write_text(
        "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(11))
    )
    result = subprocess.run(
        ["ruff", "check", *ruff_args(load()), str(source)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "complex-structure" in result.stdout
    source.write_text(
        "def f(x):\n    if x:\n        for y in x:\n"
        "            if y:\n                while y:\n                    y -= 1\n"
    )
    result = subprocess.run(
        ["ruff", "check", *ruff_args(load()), str(source)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "too-many-nested-blocks" in result.stdout


def test_import_linter_rejects_internal_imports_and_cycles(tmp_path: Path) -> None:
    for name in ["kernel", "kernel/a", "kernel/b", "distribution"]:
        directory = tmp_path / name
        directory.mkdir(exist_ok=True)
        (directory / "__init__.py").write_text("")
    (tmp_path / "kernel/a/internal.py").write_text("import kernel.b\n")
    (tmp_path / "kernel/b/__init__.py").write_text("import kernel.a.internal\n")
    (tmp_path / "distribution/__init__.py").write_text("import kernel.a.internal\n")
    config = tmp_path / ".importlinter"
    config.write_text(architecture_config([tmp_path / "kernel", tmp_path / "distribution"]))
    result = subprocess.run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "BROKEN" in result.stdout
    assert "internal" in result.stdout
    assert "no cycles in kernel BROKEN" in result.stdout
    assert "public API of kernel.a BROKEN" in result.stdout


def test_import_linter_keeps_adapters_apart_from_the_kernel_distribution_and_each_other(
    tmp_path: Path,
) -> None:
    for name in ["kernel", "distribution", "granola", "teams"]:
        (tmp_path / name).mkdir()
    (tmp_path / "kernel/__init__.py").write_text("import granola\n")
    (tmp_path / "distribution/__init__.py").write_text("import granola\nimport teams\n")
    (tmp_path / "granola/__init__.py").write_text("import kernel\nimport teams\n")
    (tmp_path / "teams/__init__.py").write_text("import kernel\nimport distribution\n")
    config = tmp_path / ".importlinter"
    packages = [tmp_path / name for name in ["kernel", "distribution", "granola", "teams"]]
    config.write_text(architecture_config(packages))
    result = subprocess.run(
        ["lint-imports", "--config", str(config), "--no-cache"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "kernel imports no adapter BROKEN" in result.stdout
    assert "granola imports neither the distribution nor another adapter BROKEN" in result.stdout
    assert "teams imports neither the distribution nor another adapter BROKEN" in result.stdout
