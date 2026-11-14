"""Respostas roteirizadas, não inteligência simulada. ADK/Runner/tools continuam reais."""

import json

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

COMPLETE = (
    "Decidido: mover 50% da alocação de Ana Silva (designer) do projeto alfa "
    "para beta a partir de 2026-11-23."
)
MISSING_DATE = "Decidido: mover 50% da alocação de Ana Silva do projeto alfa para beta."
AMBIGUOUS = "Decidido: mover 50% da alocação de Ana para beta a partir de 2026-11-23."
MOVE = {
    "person_id": "ana-design",
    "source_project": "alfa",
    "target_project": "beta",
    "percentage": 50,
    "start_date": "2026-11-23",
    "expected_version": 1,
}


class FixtureModel(BaseLlm):
    model: str = "fixture-not-a-language-model"
    capacity_tool: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        answered = {
            part.function_response.name
            for content in llm_request.contents
            for part in content.parts or []
            if part.function_response
        }
        calls = [("get_work_context", {})]
        if self.capacity_tool:
            calls.append(("get_capacity", {"project_id": "beta"}))
        for name, args in calls:
            if name not in answered:
                yield LlmResponse(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
                    )
                )
                return
        text = next(
            part.text
            for content in llm_request.contents
            if content.role == "user"
            for part in content.parts or []
            if part.text
        )
        if text == COMPLETE or text.endswith("Esclarecimento: " + COMPLETE):
            result = {"status": "proposed", "move": MOVE, "evidence": COMPLETE}
        elif text == MISSING_DATE:
            result = {
                "status": "needs_clarification",
                "question": "Qual a data de início?",
                "evidence": MISSING_DATE,
            }
        elif text == AMBIGUOUS:
            result = {
                "status": "needs_clarification",
                "question": "Qual das duas pessoas Ana?",
                "evidence": AMBIGUOUS,
            }
        else:
            raise ValueError("pedido fora das respostas roteirizadas")
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part(text=json.dumps(result, ensure_ascii=False))]
            )
        )
