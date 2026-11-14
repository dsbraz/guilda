"""Avaliação opt-in do modelo real. Ausência de credencial não vira sucesso offline."""

import asyncio
import json
import os

import pytest

from spike.agent import Proposer
from spike.fixture_model import AMBIGUOUS, COMPLETE, MISSING_DATE, MOVE
from spike.workflow import AllocationWorkflow
from tests.test_workflow import decision, request, start, stats, wait_stage

MODEL = os.environ.get("SPIKE_LIVE_MODEL")
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not MODEL, reason="defina SPIKE_LIVE_MODEL e credenciais Vertex"),
]


@pytest.mark.parametrize("text", [MISSING_DATE, AMBIGUOUS])
async def test_live_clarifies(text, tmp_path):
    result = await Proposer(MODEL).propose(text)
    (tmp_path / "model-result.json").write_text(json.dumps(result, ensure_ascii=False))
    assert result["status"] == "needs_clarification"
    assert result["move"] is None
    assert result["question"]


async def test_live_end_to_end(temporal, portal, tmp_path):
    worker, handle = await start(temporal, portal, tmp_path, request(COMPLETE), model=MODEL)
    try:
        state = await wait_stage(handle, worker, "awaiting_approval")
        assert state["envelope"]["payload"] == MOVE
        worker.kill()
        worker.start()
        await handle.execute_update(AllocationWorkflow.decide, decision(state))
        result = await asyncio.wait_for(handle.result(), 30)
        assert result["stage"] == "confirmed"
        assert await stats(portal, handle) == {"effects": 1, "attempts": 1}
        (tmp_path / "model-result.json").write_text(json.dumps(result, ensure_ascii=False))
    finally:
        worker.kill()
