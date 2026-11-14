"""Routes for tests. Deliberately not real model names or regions: the kernel takes
whatever route the deploy configures, and the tests show only that it obeys it."""

from pxos_kernel.model_access import Route

ROUTE = Route(
    provider="gemini",
    model="model-a",
    region="region-a",
    memory_model="memory-model-a",
    memory_region="memory-region-a",
    embedding_model="embedding-model-a",
    embedding_region="embedding-region-a",
)

OTHER_ROUTE = Route(
    provider="claude",
    model="model-b",
    region="region-b",
    memory_model="memory-model-b",
    memory_region="memory-region-b",
    embedding_model="embedding-model-b",
    embedding_region="embedding-region-b",
)
