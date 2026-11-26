"""Hindsight's own model calls follow the installation's route (K74, PRD §6.4).

Hindsight calls Vertex itself, for extraction and for embeddings, so the gateway cannot
sit in front of it. What keeps it on the route is its configuration, derived from the
same route as every other model call: the one the deploy configures.
"""

import pytest

from guilda_kernel.context.hindsight import main, server_settings
from guilda_kernel.model_access import Route
from model_testing import OTHER_ROUTE, ROUTE


@pytest.mark.parametrize("route", [ROUTE, OTHER_ROUTE], ids=["gemini", "claude"])
def test_the_settings_come_from_the_route(route: Route) -> None:
    settings = server_settings(route, project="p")

    assert settings["HINDSIGHT_API_LLM_MODEL"] == route.memory_model
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_REGION"] == route.memory_region
    assert settings["HINDSIGHT_API_EMBEDDINGS_GEMINI_MODEL"] == route.embedding_model
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION"] == route.embedding_region
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_PROJECT_ID"] == "p"
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_PROJECT_ID"] == "p"


def test_shell_exports_take_the_route_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = {
        "GUILDA_VERTEX_PROJECT": "p",
        "GUILDA_MODEL_PROVIDER": ROUTE.provider,
        "GUILDA_MODEL": ROUTE.model,
        "GUILDA_MODEL_REGION": ROUTE.region,
        "GUILDA_MEMORY_MODEL": ROUTE.memory_model,
        "GUILDA_MEMORY_REGION": ROUTE.memory_region,
        "GUILDA_EMBEDDING_MODEL": ROUTE.embedding_model,
        "GUILDA_EMBEDDING_REGION": ROUTE.embedding_region,
        "GUILDA_MODEL_INPUT_PRICE": str(ROUTE.input_price),
        "GUILDA_MODEL_OUTPUT_PRICE": str(ROUTE.output_price),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    main()
    expected = server_settings(ROUTE, project="p")
    exports = capsys.readouterr().out.splitlines()
    assert exports == [f"export {name}={value}" for name, value in expected.items()]
