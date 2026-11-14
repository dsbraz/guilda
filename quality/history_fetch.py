"""Fetch up to ten available main reports; history is advisory, never a waiver."""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path


def extract_reports(data: bytes, target: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        target.mkdir(parents=True, exist_ok=True)
        for name in archive.namelist():
            if name == "all.json":
                (target / name).write_bytes(archive.read(name))


def main() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        print("History unavailable: GITHUB_REPOSITORY is not set")
        return
    try:
        response = subprocess.check_output(
            [
                "gh",
                "api",
                f"repos/{repository}/actions/artifacts?name=quality-approved&per_page=100",
            ]
        )
        artifacts = json.loads(response)["artifacts"]
        available = [
            a
            for a in artifacts
            if not a["expired"] and a.get("workflow_run", {}).get("head_branch") == "main"
        ][:10]
        for artifact in available:
            data = subprocess.check_output(
                [
                    "gh",
                    "api",
                    f"repos/{repository}/actions/artifacts/{artifact['id']}/zip",
                ]
            )
            extract_reports(data, Path("quality-history") / str(artifact["id"]))
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as error:
        print(f"History unavailable: {error}")


if __name__ == "__main__":
    main()
