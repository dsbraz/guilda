"""Demonstração local: proposta → SIGKILL → aprovação → retomada → efeito único."""

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import httpx
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from spike.fixture_model import COMPLETE
from spike.local import ROOT, WorkerProcess, wait_portal
from spike.workflow import AllocationWorkflow


async def main(args):
    directory = ROOT / ".runtime" / f"demo-{uuid4()}"
    directory.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    portal_url = f"http://127.0.0.1:{port}"
    log = (directory / "portal.log").open("w")
    portal = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "spike.portal:app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env={**os.environ, "SPIKE_PORTAL_DB": str(directory / "portal.db")},
        stdout=log,
        stderr=log,
        cwd=ROOT,
    )
    worker = None
    try:
        await wait_portal(portal_url)
        async with await WorkflowEnvironment.start_local(
            download_dest_dir=str(ROOT / ".runtime"),
            dev_server_download_version="v1.10.1",
            dev_server_startup_timeout=timedelta(seconds=30),
            dev_server_database_filename=str(directory / "temporal.db"),
        ) as temporal:
            key = f"demo-{uuid4()}"
            config = json.loads((ROOT / "fixtures/agent.json").read_text())
            config["tools"].append("get_capacity")
            config_path = directory / "agent.json"
            config_path.write_text(json.dumps(config))
            worker = WorkerProcess(
                temporal.client.service_client.config.target_host,
                key,
                portal_url,
                directory,
                args.model,
                config_path,
            )
            worker.start()
            handle = await temporal.client.start_workflow(
                AllocationWorkflow.run,
                {
                    "text": COMPLETE,
                    "approver": "delivery-manager",
                    "idempotent": not args.no_idempotency,
                    "lose_response": True,
                    "reconcile_visible": not args.hide_receipt,
                },
                id=key,
                task_queue=key,
            )
            async with asyncio.timeout(150):
                while True:
                    worker.assert_alive()
                    state = await handle.query(AllocationWorkflow.status)
                    if state["stage"] == "awaiting_approval":
                        break
                    await asyncio.sleep(0.2)
            print("Proposta:", json.dumps(state, ensure_ascii=False, indent=2), flush=True)
            worker.kill()
            print("Worker morto com SIGKILL; aprovação enviada com ele fora do ar.", flush=True)
            pending = asyncio.create_task(
                handle.execute_update(
                    AllocationWorkflow.decide,
                    {
                        "actor": "delivery-manager",
                        "digest": state["digest"],
                        "decision": "approved",
                    },
                )
            )
            await asyncio.sleep(0.5)
            worker.start()
            await asyncio.wait_for(pending, 30)
            result = await asyncio.wait_for(handle.result(), 30)
            async with httpx.AsyncClient() as client:
                stats = (await client.get(f"{portal_url}/stats/{key}")).json()
            with sqlite3.connect(directory / "context.db") as db:
                events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM events")]
            history = await handle.fetch_history()
            await Replayer(workflows=[AllocationWorkflow]).replay_workflow(history)
            (directory / "history.json").write_text(history.to_json())
            summary = {
                "model": args.model,
                "result": result,
                "portal": stats,
                "context_events": events,
                "replay": "passed",
            }
            (directory / "result.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("Artefatos:", directory)
            assert stats["effects"] == 1
            expected = (
                "reconciliation_required"
                if args.no_idempotency and args.hide_receipt
                else "confirmed"
            )
            assert result["stage"] == expected
    finally:
        if worker:
            worker.kill()
        portal.terminate()
        portal.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="fixture")
    parser.add_argument("--no-idempotency", action="store_true")
    parser.add_argument("--hide-receipt", action="store_true")
    asyncio.run(main(parser.parse_args()))
