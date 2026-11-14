"""Ruff measurements for trends, including functions below the rejection limits."""

import json
import re
from pathlib import Path
from typing import Any

from quality.process import run


def measurements(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {"complexity": {}, "nesting": {}}
    names = {"C901": "complexity", "PLR1702": "nesting"}
    for row in rows:
        match = re.search(r"\((\d+) > 0\)", row["message"])
        if row["code"] not in names or match is None:
            raise ValueError(f"unexpected Ruff measurement: {row}")
        key = f"{row['filename']}:{row['location']['row']}"
        result[names[row["code"]]][key] = int(match[1])
    return result


def measure(policy: dict[str, Any], root: Path, output: Path) -> dict[str, Any]:
    log = output / "structure.json"
    result = run(
        [
            "ruff",
            "check",
            "--select",
            "C901,PLR1702",
            "--preview",
            "--ignore-noqa",
            "--config",
            "lint.mccabe.max-complexity=0",
            "--config",
            "lint.pylint.max-nested-blocks=0",
            "--output-format=json",
            *policy["lint"],
        ],
        log,
        cwd=root,
    )
    result["passed"] = result["returncode"] in {0, 1}
    try:
        rows = json.loads(log.read_text())
        for row in rows:
            row["filename"] = str(Path(row["filename"]).relative_to(root))
        result["metrics"] = measurements(rows)
    except (ValueError, KeyError, OSError) as error:
        result.update(passed=False, error=str(error))
    return result
