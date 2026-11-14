import json
from pathlib import Path

from quality.reports import history, write


def test_missing_history_warns_without_granting_approval(tmp_path: Path) -> None:
    result = history(tmp_path, {"policy_hash": "p", "metrics": {}})
    assert result["warning"]
    assert result["recent"] == []


def test_reports_remain_readable_on_failure(tmp_path: Path) -> None:
    report = {
        "mode": "fast",
        "passed": False,
        "sha": "abc",
        "policy_hash": "p",
        "checks": {"lint": False},
        "failures": ["lint failed"],
    }
    write(tmp_path, report)
    assert '"passed": false' in (tmp_path / "fast.json").read_text()
    assert "FAIL" in (tmp_path / "fast.md").read_text()


def test_history_uses_compatible_approved_main_and_reports_deltas(tmp_path: Path) -> None:
    rows = [
        {"finished_at": "2", "passed": False, "policy_hash": "p", "branch": "main"},
        {
            "finished_at": "1",
            "passed": True,
            "policy_hash": "p",
            "branch": "main",
            "metrics": {"area": {"coverage": 91}, "duration": 30, "text": "old"},
        },
    ]
    for i, row in enumerate(rows):
        directory = tmp_path / str(i)
        directory.mkdir()
        (directory / "all.json").write_text(json.dumps(row))
    (tmp_path / "all.json").write_text("invalid")
    result = history(
        tmp_path,
        {
            "policy_hash": "p",
            "metrics": {
                "area": {"coverage": 95},
                "duration": 20,
                "text": "new",
            },
        },
    )
    assert result["baseline"] == rows[1]
    assert result["recent"] == rows
    assert result["changes"] == {"area": {"coverage": 4}, "duration": -10}
