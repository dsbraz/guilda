import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quality import outcomes


@pytest.fixture(autouse=True)
def isolate_plugin_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outcomes, "_rows", [])


def test_plugin_records_strict_pending_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PXOS_QUALITY_OUTCOMES", str(tmp_path / "outcomes.json"))
    session = SimpleNamespace(config=SimpleNamespace(getoption=lambda *a, **kw: 0))
    outcomes.pytest_sessionstart(session)
    item = SimpleNamespace(get_closest_marker=lambda name: SimpleNamespace(kwargs={"strict": True}))
    report = SimpleNamespace(
        when="call",
        skipped=True,
        failed=False,
        nodeid="test_pending",
        wasxfail="PXOS-1",
        longrepr="pending",
    )
    hook = outcomes.pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    outcomes.pytest_sessionfinish(session, 0)
    rows = json.loads((tmp_path / "outcomes.json").read_text())["tests"]
    assert rows == [
        {"nodeid": "test_pending", "outcome": "xfailed", "reason": "pending", "strict": True}
    ]


def test_plugin_ignores_successful_setup_and_handles_missing_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PXOS_QUALITY_OUTCOMES", raising=False)
    hook = outcomes.pytest_runtest_makereport(None, None)
    next(hook)
    report = SimpleNamespace(when="setup", skipped=False, failed=False)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))
    outcomes.pytest_sessionfinish(None, 0)


def test_retries_fail_before_test_execution() -> None:
    session = SimpleNamespace(config=SimpleNamespace(getoption=lambda *a, **kw: 2))
    with pytest.raises(pytest.UsageError, match="retries"):
        outcomes.pytest_sessionstart(session)
    item = SimpleNamespace(nodeid="test_flaky", get_closest_marker=lambda name: True)
    with pytest.raises(pytest.UsageError, match="retries"):
        outcomes.pytest_collection_modifyitems([item])
    outcomes.pytest_collection_modifyitems([])
