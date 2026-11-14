"""Hindsight's own model calls follow the installation's route (PXOS-74, PRD §6.4).

Hindsight calls Vertex itself, for extraction and for embeddings, so the gateway cannot
sit in front of it. What keeps it inside the residency is its configuration, derived
from the same route as every other model call.
"""

import pytest

from pxos_kernel.context.hindsight import server_settings
from pxos_kernel.model_access import ROUTES, Residency


def test_a_br_installation_keeps_extraction_and_embeddings_in_sao_paulo() -> None:
    settings = server_settings(ROUTES["br"], project="p")

    assert settings["HINDSIGHT_API_LLM_VERTEXAI_REGION"] == "southamerica-east1"
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION"] == "southamerica-east1"
    for value in settings.values():
        assert value not in {"global", "us-central1"}


@pytest.mark.parametrize("residency", ["br", "global"])
def test_the_settings_come_from_the_route(residency: Residency) -> None:
    route = ROUTES[residency]
    settings = server_settings(route, project="p")

    assert settings["HINDSIGHT_API_LLM_MODEL"] == route.memory_model
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_REGION"] == route.memory_region
    assert settings["HINDSIGHT_API_EMBEDDINGS_GEMINI_MODEL"] == route.embedding_model
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION"] == route.embedding_region
    assert settings["HINDSIGHT_API_LLM_VERTEXAI_PROJECT_ID"] == "p"
    assert settings["HINDSIGHT_API_EMBEDDINGS_VERTEXAI_PROJECT_ID"] == "p"


def test_a_route_is_internally_consistent_about_residency() -> None:
    br = ROUTES["br"]
    assert {br.region, br.memory_region, br.embedding_region} == {"southamerica-east1"}
