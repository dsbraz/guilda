from pathlib import Path

import pytest

from quality.structure import measure, measurements


def test_ruff_diagnostics_keep_complexity_and_nesting_locations() -> None:
    rows = [
        {
            "code": "C901",
            "message": "`function` is too complex (7 > 0)",
            "filename": "file.py",
            "location": {"row": 2},
        },
        {
            "code": "PLR1702",
            "message": "Too many nested blocks (3 > 0)",
            "filename": "file.py",
            "location": {"row": 5},
        },
    ]
    assert measurements(rows) == {
        "complexity": {"file.py:2": 7},
        "nesting": {"file.py:5": 3},
    }


def test_measurement_uses_real_ruff_even_below_policy_limits(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def function(value):\n    if value:\n        return 1\n    return 0\n"
    )
    result = measure({"lint": ["sample.py"]}, tmp_path, tmp_path)
    assert result["passed"]
    assert result["metrics"]["complexity"]["sample.py:1"] == 2


def test_unknown_measurement_format_is_not_accepted() -> None:
    with pytest.raises(ValueError, match="unexpected Ruff"):
        measurements([{"code": "new", "message": "unknown format"}])
