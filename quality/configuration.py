"""Tool configurations derived at runtime from the policy and package inventory."""

from pathlib import Path
from typing import Any

from quality.public_imports import symbol_edges


def ruff_args(policy: dict[str, Any]) -> list[str]:
    limits = policy["limits"]
    return [
        "--preview",
        "--config",
        "lint.explicit-preview-rules=true",
        "--extend-select",
        "C901,PLR1702",
        "--config",
        f"lint.mccabe.max-complexity={limits['complexity']}",
        "--config",
        f"lint.pylint.max-nested-blocks={limits['nesting']}",
    ]


def architecture_config(packages: list[Path]) -> str:
    names = [package.name for package in packages]
    lines = ["[importlinter]", "root_packages =", *(f"    {name}" for name in names)]
    lines += [
        "",
        "[importlinter:contract:direction]",
        "name = kernel is independent",
        "type = forbidden",
        f"source_modules = {names[0]}",
        f"forbidden_modules = {names[1]}",
    ]
    for package in packages:
        lines += [
            "",
            f"[importlinter:contract:cycles-{package.name}]",
            f"name = no cycles in {package.name}",
            "type = acyclic_siblings",
            f"ancestors = {package.name}",
        ]
        for child in sorted(package.iterdir()):
            if not child.is_dir() or not (child / "__init__.py").exists():
                continue
            module = f"{package.name}.{child.name}"
            lines += [
                "",
                f"[importlinter:contract:public-{module}]",
                f"name = public API of {module}",
                "type = protected",
                f"protected_modules = {module}.*",
                f"allowed_importers = {module}",
            ]
            edges = symbol_edges(packages, child)
            if edges:
                lines += ["ignore_imports =", *(f"    {edge}" for edge in edges)]
    return "\n".join(lines) + "\n"


def coverage_config(policy: dict[str, Any]) -> str:
    return "\n".join(
        [
            "[run]",
            "branch = True",
            "source =",
            *(f"    {p}" for p in policy["sources"]),
            "omit =",
            *(f"    {p}" for p in policy["exclude"]),
            "",
        ]
    )
