"""In-memory stand-ins for the kernel's ports, shared by pxos-kernel and brq-at-work tests.

They live with the tests, not in the product: a deploy wires real adapters only.
"""

from collections import defaultdict
from collections.abc import AsyncGenerator, Callable, Iterable, Sequence
from uuid import UUID, uuid4

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from pxos_kernel.channel import Card
from pxos_kernel.context import Recalled
from pxos_kernel.ingestion import Changes, ConnectorUnavailable, SourceDocument
from pxos_kernel.model_access import Route


class FakeConnector:
    """A source system in memory: documents are put, deleted or revoked, and the delta
    since a cursor holds the last change to each source."""

    def __init__(self, name: str, documents: Iterable[SourceDocument] = ()) -> None:
        self.name = name
        self.available = True
        self._log: list[tuple[str, str, SourceDocument | None]] = []
        for document in documents:
            self.put(document)

    def put(self, document: SourceDocument) -> None:
        self._log.append((document.source, "put", document))

    def delete(self, source: str) -> None:
        self._log.append((source, "deleted", None))

    def revoke(self, source: str) -> None:
        self._log.append((source, "revoked", None))

    async def changes(self, cursor: str | None) -> Changes:
        if not self.available:
            raise ConnectorUnavailable(f"{self.name} cannot be reached")
        last = {source: (kind, doc) for source, kind, doc in self._log[int(cursor or 0) :]}
        return Changes(
            cursor=str(len(self._log)),
            documents=tuple(doc for _, doc in last.values() if doc is not None),
            deleted=tuple(source for source, (kind, _) in last.items() if kind == "deleted"),
            revoked=tuple(source for source, (kind, _) in last.items() if kind == "revoked"),
        )


class FakeChannel:
    """Reference channel: keeps each recipient's cards by proposal and each thread's posts."""

    def __init__(self) -> None:
        self.cards: dict[str, dict[UUID, Card]] = defaultdict(dict)
        self.threads: dict[str, list[str]] = defaultdict(list)

    async def deliver_card(self, card: Card) -> None:
        self.cards[card.recipient][card.proposal_id] = card

    async def post(self, thread: str, text: str) -> str:
        self.threads[thread].append(text)
        return str(uuid4())


class FakeContextStore:
    """Records what was retained, in order, and recalls everything the tags reach."""

    def __init__(self) -> None:
        self.retained: list[tuple[str, str]] = []
        self.memories: dict[str, tuple[str, frozenset[str]]] = {}

    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        self.retained.append((key, instructions))
        self.memories[key] = (text, frozenset(tags))

    async def recall(self, query: str, *, tags: Sequence[str]) -> list[Recalled]:
        return [
            Recalled(text=text, source=key)
            for key, (text, kept) in self.memories.items()
            if kept & set(tags)
        ]

    async def forget(self, source: str) -> None:
        self.memories.pop(source, None)


class FakeModels:
    """Providers for tests: every model answers ``reply`` (a text, or a function of the
    request giving a text or a part, such as a tool call), reports ``tokens`` (input,
    output) when given, and records the requests it got and the route it served."""

    def __init__(
        self,
        reply: str | Callable[[LlmRequest], str | types.Part],
        tokens: tuple[int, int] | None = None,
    ) -> None:
        self.reply = reply
        self.tokens = tokens
        self.routes: list[Route] = []
        self.requests: list[LlmRequest] = []

    def __call__(self, route: Route) -> BaseLlm:
        return _FakeModel(route, self)


class _FakeModel(BaseLlm):
    _route: Route = PrivateAttr()
    _models: FakeModels = PrivateAttr()

    def __init__(self, route: Route, models: FakeModels) -> None:
        super().__init__(model=route.model)
        self._route = route
        self._models = models

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._models.routes.append(self._route)
        self._models.requests.append(llm_request)
        reply = self._models.reply
        answer = reply if isinstance(reply, str) else reply(llm_request)
        part = types.Part(text=answer) if isinstance(answer, str) else answer
        tokens = self._models.tokens
        usage = None
        if tokens is not None:
            usage = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=tokens[0], candidates_token_count=tokens[1]
            )
        yield LlmResponse(content=types.Content(role="model", parts=[part]), usage_metadata=usage)
