from quality.outcomes import check_outcomes
from quality.policy import load


def test_unexpected_skip_fails() -> None:
    assert check_outcomes(
        [{"nodeid": "new.py::test_x", "outcome": "skipped", "reason": "skip"}], load()
    )


def test_explicit_external_skip_is_allowed() -> None:
    assert (
        check_outcomes(
            [
                {
                    "nodeid": "pxos-kernel/tests/test_vertex_routes.py::test_route",
                    "outcome": "skipped",
                    "reason": "PXOS_VERTEX_PROJECT not set",
                }
            ],
            load(),
        )
        == []
    )


def test_xfail_requires_pending_requirement_and_strict_marker() -> None:
    row = {
        "nodeid": "pxos-kernel/tests/acceptance/test_requirements.py::test_requirement_2",
        "outcome": "xfailed",
        "strict": True,
    }
    assert check_outcomes([row], load()) == []
    assert check_outcomes([{**row, "strict": False}], load())
    assert check_outcomes([{**row, "nodeid": "other.py::test_x"}], load())


def test_the_mvp_scenarios_may_be_pending() -> None:
    row = {
        "nodeid": "brq-at-work/tests/test_mvp_scenarios.py::test_resumption",
        "outcome": "xfailed",
        "strict": True,
    }
    assert check_outcomes([row], load()) == []
