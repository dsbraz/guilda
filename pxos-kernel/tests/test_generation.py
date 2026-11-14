import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pxos_kernel.agents.versions import PACKAGE_DIR as AGENTS
from pxos_kernel.ontology.generate import JSON_SCHEMA_FILE, MODELS_FILE, main, stale_files
from pxos_kernel.ontology.loader import BASE_DIR

RETAIL = Path(__file__).parent / "retail_fixture"


def test_generated_files_match_linkml() -> None:
    """Snapshot: regenerate with `python -m pxos_kernel.ontology.generate <dirs>`."""
    assert stale_files([RETAIL, AGENTS]) == []


def test_generated_files_live_next_to_their_package() -> None:
    for package_dir in (BASE_DIR, RETAIL, AGENTS):
        assert (package_dir / MODELS_FILE).is_file()
        assert (package_dir / JSON_SCHEMA_FILE).is_file()


def test_generated_model_validates_fixture_payload() -> None:
    from retail_fixture.generated import RestockOrder

    order = RestockOrder(sku="A-1", quantity=3, occurred_at=datetime(2026, 11, 13, tzinfo=UTC))
    assert order.quantity == 3


def test_the_generator_command_checks_and_rewrites_a_package(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    package = tmp_path / "retail"
    shutil.copytree(RETAIL, package)
    assert main(["--check", str(package)]) == 0

    (package / MODELS_FILE).write_text("# stale\n", encoding="utf-8")
    assert main(["--check", str(package)]) == 1
    assert f"stale: {package / MODELS_FILE}" in capsys.readouterr().err

    assert main([str(package)]) == 0
    assert main(["--check", str(package)]) == 0
