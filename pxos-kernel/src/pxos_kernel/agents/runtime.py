"""The ADK step of an agent version (PXOS-79, K34; ADR 0004 §2).

The kernel is the only place that builds ADK agents, so every agent gets its model from
the gateway. One ADK agent reads one extracted fact at a time, in a session of its own,
and answers with the payload of the action its version proposes plus the literal quote.
The answer is untrusted: anything that does not parse into that shape is no candidate.
"""

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ValidationError, create_model

from pxos_kernel.agents.versions import AgentVersion

APP = "pxos"


class FactReader:
    """Reads facts with the version's instructions, on the model the gateway gave."""

    def __init__(self, model: BaseLlm, version: AgentVersion, payload: type[BaseModel]) -> None:
        self._version = version
        optional: Any = payload | None
        self._answer = create_model("Candidate", payload=(optional, None), quote=(str | None, None))

        def instructions(context: ReadonlyContext) -> str:
            return version.instructions  # literal: braces are text, not ADK state

        self._agent = LlmAgent(
            name=version.agent.replace("-", "_"),
            model=model,
            instruction=instructions,
            output_schema=self._answer,
        )

    async def read(self, fact: str) -> tuple[dict[str, Any], str] | None:
        """The payload and quote the agent read from ``fact``, or None."""
        sessions = InMemorySessionService()
        runner = Runner(agent=self._agent, app_name=APP, session_service=sessions)
        try:
            session = await sessions.create_session(app_name=APP, user_id=self._version.agent)
            answer = ""
            async for event in runner.run_async(
                user_id=self._version.agent,
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=fact)]),
            ):
                if event.is_final_response() and event.content:
                    answer = "".join(part.text or "" for part in event.content.parts or [])
        finally:
            await runner.close()
        try:
            parsed = self._answer.model_validate_json(answer)
        except ValidationError:
            return None
        payload, quote = parsed.payload, parsed.quote  # type: ignore[attr-defined]
        if payload is None or not quote:
            return None
        return payload.model_dump(mode="json", exclude_none=True), quote
