"""The ADK agent of an approved version (ADR 0004 §2, rule 1; ADR 0006 §1, §2).

The only kernel module that builds ADK agents: each gets the model a gateway issued and
only the tools the kernel builds. A round reads the cycle's inputs as data and answers
either questions for the thread or the payload of the version's deliverable. The answer
is untrusted: anything else is refused, never proposed.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.function_tool import FunctionTool
from google.genai import types
from pydantic import BaseModel, ValidationError, create_model

from guilda_kernel.agents.versions import AgentVersion
from guilda_kernel.context import ContextStore
from guilda_kernel.errors import GuildaError
from guilda_kernel.identity import Connection
from guilda_kernel.model_access import GatewayModel, ModelNotAllowed, Usage, issued_by_gateway
from guilda_kernel.ontology import Registry

APP = "guilda"

Recall = Callable[[str], Awaitable[Sequence[tuple[str, str]]]]
"""Searches the context memory, in a scope the kernel bound, for (text, source) pairs."""


class NotAnAgent(GuildaError):
    pass


class MissingTool(GuildaError):
    pass


class InvalidOutput(GuildaError):
    """The round answered neither questions nor a valid deliverable."""


@dataclass(frozen=True)
class RoundInput:
    answers: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = ()
    final_round: bool = False
    """The cycle has no round after this one: the agent must deliver, with what it could
    not confirm as uncertainties."""


@dataclass(frozen=True)
class RoundOutput:
    """Either questions for the thread or the deliverable's payload."""

    questions: tuple[str, ...] = ()
    deliverable: dict[str, Any] | None = None


def scoped_recall(store: ContextStore, version: AgentVersion) -> Recall:
    """The search the kernel binds for ``version``: only within its recall tags."""

    async def search(query: str) -> list[tuple[str, str]]:
        found = await store.recall(query, tags=version.recall_tags)
        return [(recalled.text, recalled.source) for recalled in found]

    return search


def recall_tool(search: Recall) -> FunctionTool:
    """The recall tool: the model chooses only the query."""

    async def recall(query: str) -> dict[str, Any]:
        """Searches the context memory; each result has its text and its source."""
        found = await search(query)
        return {"results": [{"text": text, "source": source} for text, source in found]}

    return FunctionTool(recall)


class Agent:
    def __init__(
        self, version: AgentVersion, agent: LlmAgent, answer: type[BaseModel], model: GatewayModel
    ) -> None:
        self.version = version
        self._agent = agent
        self._answer = answer
        self._model = model

    def usage(self) -> Usage:
        """The model, tokens and cost of the agent's rounds so far, as the gateway counted."""
        return self._model.usage()

    async def answer(self, round_input: RoundInput) -> RoundOutput:
        """One round on ``round_input``; raises InvalidOutput on any other answer."""
        text = await self._run(json.dumps(asdict(round_input), ensure_ascii=False))
        try:
            parsed: Any = self._answer.model_validate_json(text)
        except ValidationError:
            raise InvalidOutput("the round's answer does not match its schema") from None
        questions, deliverable = tuple(parsed.questions or ()), parsed.deliverable
        if bool(questions) == (deliverable is not None):
            raise InvalidOutput("a round answers either questions or the deliverable")
        if round_input.final_round and deliverable is None:
            raise InvalidOutput("the last round of a cycle answers the deliverable")
        if deliverable is None:
            return RoundOutput(questions=questions)
        return RoundOutput(deliverable=deliverable.model_dump(mode="json", exclude_none=True))

    async def _run(self, message: str) -> str:
        sessions = InMemorySessionService()
        runner = Runner(agent=self._agent, app_name=APP, session_service=sessions)
        try:
            session = await sessions.create_session(app_name=APP, user_id=self.version.agent)
            text = ""
            async for event in runner.run_async(
                user_id=self.version.agent,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=message)]),
            ):
                if event.is_final_response() and event.content:
                    text = "".join(part.text or "" for part in event.content.parts or [])
        finally:
            await runner.close()
        return text


async def build_agent(
    connection: Connection,
    model: GatewayModel,
    registry: Registry,
    version: AgentVersion,
    *,
    recall: Recall | None = None,
) -> Agent:
    """The agent of ``version``, on ``model``, which a gateway must have issued."""
    if not issued_by_gateway(model):
        raise ModelNotAllowed("an agent gets its model from the gateway, and only from it")
    kind = await connection.fetchval("SELECT kind FROM guilda.actors WHERE id = $1", version.agent)
    if kind != "agent":
        raise NotAnAgent(f"{version.agent} is not an agent")
    tools: list[Any] = []
    if "recall" in version.tools:
        if recall is None:
            raise MissingTool(f"{version.agent} uses recall, and no search was given")
        tools.append(recall_tool(recall))
    package, action = version.deliverable
    registered = registry.get(package)
    payload: Any = registered.models[registered.package.action(action).creates] | None
    answer = create_model("Round", questions=(list[str] | None, None), deliverable=(payload, None))

    def instructions(context: ReadonlyContext) -> str:
        return version.instructions  # literal: braces are text, not ADK state

    agent = LlmAgent(
        name="agent",  # an ADK name must be an identifier; actor ids need not be
        model=model,
        instruction=instructions,
        tools=tools,
        output_schema=answer,
    )
    return Agent(version, agent, answer, model)
