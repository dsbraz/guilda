"""Hindsight adapter for the context port (PXOS-53), over its HTTP API.

The bank is configured for custom extraction with the distribution's instructions, without
observations (consolidated summaries). Retaining is synchronous and keyed by document id:
retaining a key again replaces its memories, and forgetting deletes the document with all
its memories. Recall excludes untagged memories (``any_strict``) and observations, so each
result comes from a document the tags reach. A network failure becomes ContextUnavailable,
by type alone; any other error from Hindsight is raised as is.
"""

import os
from collections.abc import Sequence
from typing import Any, Self
from urllib.parse import quote

import httpx

from guilda_kernel.context import ContextUnavailable, Recalled
from guilda_kernel.model_access import Route


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
            os.environ["GUILDA_HINDSIGHT_URL"],
            os.environ.get("GUILDA_HINDSIGHT_BANK", "pxos-local"),
            os.environ.get("GUILDA_HINDSIGHT_API_KEY"),
        )

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, f"{self._bank}{path}", **kwargs)
        except httpx.TransportError as error:
            raise ContextUnavailable(type(error).__name__) from None

    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        config = await self._send(
            "PATCH",
            "/config",
            json={
                "updates": {
                    "retain_extraction_mode": "custom",
                    "retain_custom_instructions": instructions,
                    "enable_observations": False,
                }
            },
        )
        config.raise_for_status()
        retained = await self._send(
            "POST",
            "/memories",
            json={
                "items": [{"content": text, "document_id": key, "tags": list(tags)}],
                "async": False,
            },
        )
        retained.raise_for_status()

    async def recall(self, query: str, *, tags: Sequence[str]) -> list[Recalled]:
        if not tags:
            return []
        response = await self._send(
            "POST",
            "/memories/recall",
            json={
                "query": query,
                "tags": list(tags),
                "tags_match": "any_strict",
                "types": ["world", "experience"],
            },
        )
        response.raise_for_status()
        return [
            Recalled(text=result["text"], source=result["document_id"])
            for result in response.json()["results"]
        ]

    async def forget(self, source: str) -> None:
        response = await self._send("DELETE", f"/documents/{quote(source, safe='')}")
        if response.status_code != httpx.codes.NOT_FOUND:
            response.raise_for_status()


def main() -> None:
    """Shell exports for scripts/hindsight-local.sh, from the route the environment configures."""
    settings = server_settings(
        Route.from_environment(), project=os.environ["GUILDA_VERTEX_PROJECT"]
    )
    for name, value in settings.items():
        print(f"export {name}={value}")
