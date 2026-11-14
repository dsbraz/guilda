"""Record command evidence even when infrastructure or a check fails."""

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Any


def run(
    command: list[str],
    log: Path,
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log.open("w") as output:
            result = subprocess.run(
                command,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        code = result.returncode
    except OSError as error:
        log.write_text(str(error))
        code = -1
    return {
        "command": command,
        "returncode": code,
        "passed": code == 0,
        "seconds": time.monotonic() - started,
        "log": str(log),
    }


def git(command: list[str], root: Path) -> str:
    return subprocess.check_output(["git", *command], cwd=root, text=True).strip()


def workspace(root: Path) -> list[str]:
    return git(["status", "--porcelain", "--untracked-files=all"], root).splitlines()


def fingerprint(root: Path) -> str:
    content = hashlib.sha256(git(["diff", "HEAD", "--binary"], root).encode())
    for name in git(["ls-files", "--others", "--exclude-standard"], root).splitlines():
        content.update(name.encode())
        content.update((root / name).read_bytes())
    return content.hexdigest()
