import asyncio
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest
from temporalio.client import WorkflowUpdateFailedError
from temporalio.worker import Replayer

from spike.domain import digest
from spike.fixture_model import AMBIGUOUS, COMPLETE, MISSING_DATE
from spike.local import ROOT, WorkerProcess, wait_portal
from spike.workflow import AllocationWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def wait_stage(handle, worker, expected):
    for _ in range(600):
        worker.assert_alive()
        state = await handle.query(AllocationWorkflow.status)
        if state["stage"] == expected:
            return state
        await asyncio.sleep(0.1)
    raise AssertionError(f"esperado {expected}, recebido {state}")


def request(text=COMPLETE, **options):
    return {
        "text": text,
        "approver": "delivery-manager",
        "idempotent": True,
        "lose_response": False,
        "reconcile_visible": True,
        **options,
    }


def decision(state, **overrides):
    return {
        "actor": "delivery-manager",
        "digest": state["digest"],
        "decision": "approved",
        **overrides,
    }


async def start(temporal, portal, tmp_path, data, config=None, model="fixture"):
    await wait_portal(portal)
    queue = f"spike-{uuid4()}"
    worker = WorkerProcess(
        temporal.client.service_client.config.target_host,
        queue,
        portal,
        tmp_path,
        model,
        config,
    )
    worker.start()
    handle = await temporal.client.start_workflow(
        AllocationWorkflow.run,
        data,
        id=queue,
        task_queue=queue,
    )
    return worker, handle


async def stats(portal, handle):
    async with httpx.AsyncClient() as client:
        return (await client.get(f"{portal}/stats/{handle.id}")).json()


@pytest.mark.parametrize(
    "idempotent,visible,stage,attempts",
    [
        (True, True, "confirmed", 2),
        (False, True, "confirmed", 1),
        (False, False, "reconciliation_required", 1),
    ],
)
async def test_lost_response(temporal, portal, tmp_path, idempotent, visible, stage, attempts):
    worker, handle = await start(
        temporal,
        portal,
        tmp_path,
        request(
            idempotent=idempotent,
            lose_response=True,
            reconcile_visible=visible,
        ),
    )
    try:
        state = await wait_stage(handle, worker, "awaiting_approval")
        assert await stats(portal, handle) == {"effects": 0, "attempts": 0}
        await handle.execute_update(AllocationWorkflow.decide, decision(state))
        result = await asyncio.wait_for(handle.result(), 30)
        assert result["stage"] == stage
        assert await stats(portal, handle) == {"effects": 1, "attempts": attempts}
        with sqlite3.connect(tmp_path / "context.db") as db:
            events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events")]
        assert [event["stage"] for event in events] == ["proposed", "approved", stage]
        history = await handle.fetch_history()
        await Replayer(workflows=[AllocationWorkflow]).replay_workflow(history)
        (tmp_path / "history.json").write_text(history.to_json())
    finally:
        worker.kill()


async def test_restart_exact_approval_and_fde_extension(temporal, portal, tmp_path):
    config = json.loads((ROOT / "fixtures/agent.json").read_text())
    config["tools"].append("get_capacity")
    config_path = tmp_path / "agent.json"
    config_path.write_text(json.dumps(config))
    worker, handle = await start(temporal, portal, tmp_path, request(), config_path)
    try:
        state = await wait_stage(handle, worker, "awaiting_approval")
        assert state["tools_used"] == ["get_work_context", "get_capacity"]
        worker.kill()  # SIGKILL real: perde memória e sessão ADK.
        # Temporal recebe a aprovação com worker fora do ar.
        pending = asyncio.create_task(
            handle.execute_update(AllocationWorkflow.decide, decision(state))
        )
        await asyncio.sleep(0.2)
        assert not pending.done()
        worker.start()
        await asyncio.wait_for(pending, 30)
        result = await asyncio.wait_for(handle.result(), 30)
        assert result["stage"] == "confirmed"
        assert await stats(portal, handle) == {"effects": 1, "attempts": 1}
        history = await handle.fetch_history()
        scheduled = [
            e.activity_task_scheduled_event_attributes.activity_type.name
            for e in history.events
            if e.HasField("activity_task_scheduled_event_attributes")
        ]
        assert scheduled.count("propose") == 1  # replay não consulta o modelo novamente.
        await Replayer(workflows=[AllocationWorkflow]).replay_workflow(history)
    finally:
        worker.kill()


async def test_invalid_approvals_and_rejection(temporal, portal, tmp_path):
    worker, handle = await start(temporal, portal, tmp_path, request())
    try:
        state = await wait_stage(handle, worker, "awaiting_approval")
        invalid = [decision(state, actor="intruder"), decision(state, decision="maybe")]
        for field, value in [
            ("person_id", "ana-research"),
            ("target_project", "gama"),
            ("percentage", 80),
            ("start_date", "2026-12-01"),
        ]:
            envelope = json.loads(json.dumps(state["envelope"]))
            envelope["payload"][field] = value
            invalid.append(decision(state, digest=digest(envelope)))
        invalid.append({**decision(state), "payload": {"percentage": 80}})
        for attempt in invalid:
            with pytest.raises(WorkflowUpdateFailedError):
                await handle.execute_update(AllocationWorkflow.decide, attempt)
        assert await stats(portal, handle) == {"effects": 0, "attempts": 0}
        await handle.execute_update(AllocationWorkflow.decide, decision(state, decision="rejected"))
        assert (await handle.result())["stage"] == "rejected"
        assert await stats(portal, handle) == {"effects": 0, "attempts": 0}
    finally:
        worker.kill()


@pytest.mark.parametrize("text", [MISSING_DATE, AMBIGUOUS])
async def test_clarification_survives_restart(temporal, portal, tmp_path, text):
    worker, handle = await start(temporal, portal, tmp_path, request(text))
    try:
        state = await wait_stage(handle, worker, "needs_clarification")
        assert state["question"]
        assert await stats(portal, handle) == {"effects": 0, "attempts": 0}
        worker.kill()
        worker.start()
        await handle.execute_update(AllocationWorkflow.clarify, COMPLETE)
        state = await wait_stage(handle, worker, "awaiting_approval")
        await handle.execute_update(AllocationWorkflow.decide, decision(state))
        assert (await handle.result())["stage"] == "confirmed"
    finally:
        worker.kill()
