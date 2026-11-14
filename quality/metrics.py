"""Measurements with no process or filesystem side effects."""

import ast
import io
import tokenize
from collections import Counter
from pathlib import Path
from typing import Any


def size_metrics(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            docstrings.update(range(value.lineno, (value.end_lineno or value.lineno) + 1))
    lines: set[int] = set()
    ignored = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in ignored:
            lines.update(range(token.start[0], token.end[0] + 1))
    lines -= docstrings
    physical = source.splitlines()
    lines = {line for line in lines if physical[line - 1].strip()}
    functions = {
        f"{node.name}:{node.lineno}": len(lines & set(range(node.lineno, node.end_lineno + 1)))
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno
    }
    return {"lines": len(lines), "functions": functions}


def coverage_metrics(
    data: dict[str, Any],
    areas: dict[str, list[Path]],
    root: Path,
) -> dict[str, dict[str, float]]:
    measured = {(root / name).resolve(): value for name, value in data["files"].items()}
    results = {}
    for area, files in areas.items():
        if not files:
            raise ValueError(f"empty coverage area: {area}")
        totals: Counter[str] = Counter()
        for file in files:
            if file.resolve() not in measured:
                raise ValueError(f"unmeasured file: {file}")
            totals.update(measured[file.resolve()]["summary"])
        results[area] = {
            "line_coverage": _percentage(totals["covered_lines"], totals["num_statements"]),
            "branch_coverage": _percentage(totals["covered_branches"], totals["num_branches"]),
        }
    return results


def _percentage(covered: int, total: int) -> float:
    return 100.0 * covered / total if total else 100.0


def mutation_metrics(statuses: list[str]) -> dict[str, Any]:
    counts: Counter[str] = Counter(statuses)
    complete = bool(statuses) and set(counts) <= {"killed", "survived", "no tests"}
    return {
        "killed": counts["killed"],
        "survived": counts["survived"],
        "uncovered": counts["no tests"],
        "timeouts": counts["timeout"],
        "invalid": sum(
            value
            for key, value in counts.items()
            if key not in {"killed", "survived", "no tests", "timeout", "not checked"}
        ),
        "pending": counts["not checked"],
        "total": len(statuses),
        "score": _percentage(counts["killed"], len(statuses)) if statuses else 0,
        "complete": complete,
    }
