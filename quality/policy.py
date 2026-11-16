"""Single source of limits and dynamically discovered coverage areas."""

import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def load(root: Path = ROOT) -> dict[str, Any]:
    return dict(json.loads((root / "quality.policy.json").read_text()))


def digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def excluded(path: str, policy: dict[str, Any]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in policy["exclude"])


def areas(policy: dict[str, Any], root: Path = ROOT) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for source in policy["sources"]:
        directory = root / source
        if not directory.is_dir():
            raise ValueError(f"missing source directory: {source}")
        for file in sorted(directory.rglob("*.py")):
            if excluded(file.relative_to(root).as_posix(), policy):
                continue
            relative = file.relative_to(directory)
            area = "automation" if source == "quality" else "distribution"
            if source in policy.get("adapters", []):
                area = directory.name
            if directory.name == "pxos_kernel":
                area = relative.parts[0] if len(relative.parts) > 1 else "services"
            result.setdefault(area, []).append(file)
    return result
