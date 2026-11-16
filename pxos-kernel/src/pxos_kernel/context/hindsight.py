"""Hindsight adapter for the context port (PXOS-53), over its HTTP API.

The bank is configured for custom extraction with the distribution's instructions, without
observations (consolidated summaries). Retaining is synchronous and keyed by document id,
so a replay of an unchanged document keeps its memories as they are.
"""

import os
from typing import Self

import httpx

from pxos_kernel.model_access import Route


def server_settings(route: Route, *, project: str) -> dict[str, str]:
    """The Hindsight server's model and embedding settings for an installation's route.
    Hindsight calls Vertex itself, so this is what keeps it on the route."""
    return {
        "HINDSIGHT_API_LLM_PROVIDER": "vertexai",
        "HINDSIGHT_API_LLM_VERTEXAI_PROJECT_ID": project,
        "HINDSIGHT_API_LLM_VERTEXAI_REGION": route.memory_region,
        "HINDSIGHT_API_LLM_MODEL": route.memory_model,
        "HINDSIGHT_API_EMBEDDINGS_PROVIDER": "google",
        "HINDSIGHT_API_EMBEDDINGS_VERTEXAI_PROJECT_ID": project,
        "HINDSIGHT_API_EMBEDDINGS_VERTEXAI_REGION": route.embedding_region,
        "HINDSIGHT_API_EMBEDDINGS_GEMINI_MODEL": route.embedding_model,
    }


class HindsightContextStore:
    def __init__(
        self,
        base_url: str,
        bank: str,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=600, transport=transport
        )
        self._bank = f"/v1/default/banks/{bank}"

    @classmethod
    def from_environment(cls) -> Self:
        return cls(
            os.environ["PXOS_HINDSIGHT_URL"],
            os.environ.get("PXOS_HINDSIGHT_BANK", "pxos-local"),
            os.environ.get("PXOS_HINDSIGHT_API_KEY"),
        )

    async def retain(self, key: str, text: str, instructions: str) -> None:
        config = await self._client.patch(
            f"{self._bank}/config",
            json={
                "updates": {
                    "retain_extraction_mode": "custom",
                    "retain_custom_instructions": instructions,
                    "enable_observations": False,
                }
            },
        )
        config.raise_for_status()
        retained = await self._client.post(
            f"{self._bank}/memories",
            json={"items": [{"content": text, "document_id": key}], "async": False},
        )
        retained.raise_for_status()


def main() -> None:
    """Shell exports for scripts/hindsight-local.sh, from the route the environment configures."""
    settings = server_settings(Route.from_environment(), project=os.environ["PXOS_VERTEX_PROJECT"])
    for name, value in settings.items():
        print(f"export {name}={value}")
