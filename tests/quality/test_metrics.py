import json
from pathlib import Path

import pytest

from quality.metrics import coverage_metrics, mutation_metrics, size_metrics


def test_size_ignores_comments_blank_lines_and_docstrings_but_counts_sql() -> None:
    source = (
        '"""module"""\n# comment\n\ndef f():\n    """doc\n    string"""\n'
        '    sql = """SELECT\n        * FROM facts\n    """\n    return sql\n'
    )
    assert size_metrics(source) == {"lines": 5, "functions": {"f:4": 5}}


def test_nested_functions_are_measured() -> None:
    result = size_metrics("def f():\n    def g():\n        return 1\n    return g()\n")
    assert result["functions"] == {"f:1": 4, "g:2": 2}


def test_blank_lines_inside_embedded_sql_are_not_code_lines() -> None:
    assert size_metrics('query = """SELECT\n\n  * FROM facts\n"""\n')["lines"] == 3


def test_coverage_requires_every_file_even_when_never_imported(tmp_path: Path) -> None:
    file = tmp_path / "never.py"
    file.write_text("answer = 42\n")
    with pytest.raises(ValueError, match="never.py"):
        coverage_metrics({"files": {}}, {"services": [file]}, tmp_path)


def test_coverage_counts_lines_and_branches_separately(tmp_path: Path) -> None:
    data = {
        "files": {
            "a.py": {
                "summary": {
                    "covered_lines": 9,
                    "num_statements": 10,
                    "covered_branches": 3,
                    "num_branches": 4,
                }
            }
        }
    }
    assert coverage_metrics(data, {"a": [tmp_path / "a.py"]}, tmp_path)["a"] == {
        "line_coverage": 90.0,
        "branch_coverage": 75.0,
    }


def test_empty_coverage_area_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        coverage_metrics({"files": {}}, {"empty": []}, tmp_path)


def test_mutation_keeps_failure_categories_separate() -> None:
    result = mutation_metrics(["killed"] * 8 + ["survived", "no tests"])
    assert result["score"] == 80
    assert result["survived"] == result["uncovered"] == 1
    assert result["complete"] is True


@pytest.mark.parametrize(
    "statuses", [[], ["timeout"], ["not checked"], ["suspicious"], ["invalid"]]
)
def test_empty_or_incomplete_mutation_never_approves(statuses: list[str]) -> None:
    assert mutation_metrics(statuses)["complete"] is False


def test_policy_is_json() -> None:
    assert json.loads(Path("quality.policy.json").read_text())["limits"]["test_retries"] == 0
