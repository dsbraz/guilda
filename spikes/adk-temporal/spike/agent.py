"""ADK em uma Activity: sessão efêmera, contexto explícito, ferramentas só de leitura."""

import json
from pathlib import Path
from uuid import uuid4

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from spike.domain import Proposal
from spike.fde_tools import tool_registry

ROOT = Path(__file__).resolve().parents[1]


class Proposer:
    def __init__(self, model, config=None, context=None):
        self.model = model
        self.config = config or json.loads((ROOT / "fixtures/agent.json").read_text())
        self.context = context or json.loads((ROOT / "fixtures/context.json").read_text())

    async def propose(self, request: str, *, metrics: dict | None = None) -> dict:
        used = []

        # Registro de extensão do FDE. Não modifica o workflow nem expõe execução ao LLM.
        registry = tool_registry(self.context, used)
        agent = LlmAgent(
            name=self.config["name"],
            model=self.model,
            instruction=self.config["instruction"]
            + "\nSchema de saída: "
            + json.dumps(Proposal.model_json_schema()),
            tools=[registry[name] for name in self.config["tools"]],
            generate_content_config=types.GenerateContentConfig(temperature=0),
        )
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="spike", user_id="fixture-dm")
        runner = Runner(agent=agent, app_name="spike", session_service=sessions)
        final = ""
        try:
            async for event in runner.run_async(
                user_id="fixture-dm",
                session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=request)]),
                run_config=RunConfig(max_llm_calls=6),
            ):
                if metrics is not None and event.usage_metadata:
                    usage = event.usage_metadata.model_dump(exclude_none=True)
                    metrics.setdefault("usage", []).append(usage)
                if event.is_final_response() and event.content:
                    final = "".join(part.text or "" for part in event.content.parts or [])
        finally:
            await runner.close()
        proposal = Proposal.model_validate_json(final)
        if "get_work_context" not in used:
            raise ValueError("agente não consultou contexto")
        if not proposal.evidence or proposal.evidence not in request:
            raise ValueError("evidência não é literal no pedido")
        if proposal.move:
            move = proposal.move
            people = {person["id"]: person for person in self.context["people"]}
            person = people.get(move.person_id)
            if (
                person is None
                or move.source_project != person["project"]
                or move.expected_version != person["version"]
                or move.percentage > person["percentage"]
                or move.target_project not in self.context["projects"]
            ):
                raise ValueError("proposta incompatível com o contexto")
        return {**proposal.model_dump(mode="json"), "tools_used": used, "run_id": str(uuid4())}
