import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from quality import history_fetch
from quality.history_fetch import extract_reports


def test_history_extracts_only_report_json(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("all.json", "{}")
        archive.writestr("../../evil", "ignored")
    extract_reports(buffer.getvalue(), tmp_path)
    assert (tmp_path / "all.json").read_text() == "{}"
    assert list(tmp_path.iterdir()) == [tmp_path / "all.json"]


def test_invalid_archive_fails() -> None:
    with pytest.raises(zipfile.BadZipFile):
        extract_reports(b"not zip", Path("unused"))


def test_history_fetch_handles_missing_repository(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    history_fetch.main()
    assert "unavailable" in capsys.readouterr().out


def test_history_fetch_downloads_available_main_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("all.json", "{}")
    responses = iter(
        [
            json.dumps(
                {
                    "artifacts": [
                        {"id": 1, "expired": False, "workflow_run": {"head_branch": "main"}},
                        {"id": 2, "expired": True},
                    ]
                }
            ).encode(),
            buffer.getvalue(),
        ]
    )
    monkeypatch.setattr(subprocess, "check_output", lambda *args: next(responses))
    history_fetch.main()
    assert (tmp_path / "quality-history/1/all.json").read_text() == "{}"


def test_history_api_failure_is_advisory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    def fail(*args: Any) -> Any:
        raise OSError("offline")

    monkeypatch.setattr(subprocess, "check_output", fail)
    history_fetch.main()
    assert "offline" in capsys.readouterr().out
