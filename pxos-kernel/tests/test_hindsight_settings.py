"""Hindsight's own model calls follow the installation's route (PXOS-74, PRD §6.4).

Hindsight calls Vertex itself, for extraction and for embeddings, so the gateway cannot
sit in front of it. What keeps it on the route is its configuration, derived from the
same route as every other model call: the one the deploy configures.
"""

import pytest

from model_testing import OTHER_ROUTE, ROUTE
from pxos_kernel.context.hindsight import main, server_settings
from pxos_kernel.model_access import Route


@pytest.mark.parametrize("route", [ROUTE, OTHER_ROUTE], ids=["gemini", "claude"])
def test_the_settings_come_from_the_route(route: Route) -> None:
    settings = server_settings(route, project="p")

    assert settings["HINDSIGHT_API_LLM_MODEL"] == route.memory_model
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_REGION"] == route.memory_region
    assert settings["HINDSIGHT_API_EMBEDDINGS_GEMINI_MODEL"] == route.embedding_model
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION"] == route.embedding_region
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_PROJECT_ID"] == "p"
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_PROJECT_ID"] == "p"


def test_the_local_script_takes_the_route_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """scripts/hindsight-local.sh evaluates these exports."""
    environment = {
        "PXOS_VERTEX_PROJECT": "p",
        "PXOS_MODEL_PROVIDER": ROUTE.provider,
        "PXOS_MODEL": ROUTE.model,
        "PXOS_MODEL_REGION": ROUTE.region,
        "PXOS_MEMORY_MODEL": ROUTE.memory_model,
        "PXOS_MEMORY_REGION": ROUTE.memory_region,
        "PXOS_EMBEDDING_MODEL": ROUTE.embedding_model,
        "PXOS_EMBEDDING_REGION": ROUTE.embedding_region,
        "PXOS_MODEL_INPUT_PRICE": str(ROUTE.input_price),
        "PXOS_MODEL_OUTPUT_PRICE": str(ROUTE.output_price),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    main()
    expected = server_settings(ROUTE, project="p")
    exports = capsys.readouterr().out.splitlines()
    assert exports == [f"export {name}={value}" for name, value in expected.items()]
