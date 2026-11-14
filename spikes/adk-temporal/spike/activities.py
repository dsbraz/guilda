"""Fronteiras externas: modelo, conector HTTP e diário de contexto de fixture."""

import json
import sqlite3

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from spike.domain import digest


class Activities:
    def __init__(self, proposer, portal_url: str, journal: str):
        self.proposer = proposer
        self.portal_url = portal_url
        self.journal = journal
        with sqlite3.connect(journal) as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, payload TEXT)")

    @activity.defn
    async def propose(self, request: str) -> dict:
        return await self.proposer.propose(request)

    @activity.defn
    async def execute(self, command: dict) -> dict:
        # A fronteira não aceita payload diferente do que foi aprovado.
        if digest(command["envelope"]) != command["approved_digest"]:
            raise ApplicationError("digest divergente", non_retryable=True)
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{self.portal_url}/moves",
                json={
                    "operation_id": command["envelope"]["operation_id"],
                    "payload": command["envelope"]["payload"],
                    "idempotent": command["idempotent"],
                    "lose_response": command["lose_response"],
                },
            )
            if 400 <= response.status_code < 500:
                raise ApplicationError("portal recusou execução", non_retryable=True)
            response.raise_for_status()
            receipt = response.json()
        if (
            receipt.get("payload_digest") != digest(command["envelope"]["payload"])
            or receipt.get("operation_id") != command["envelope"]["operation_id"]
            or receipt.get("confirmed") is not True
        ):
            raise ApplicationError("comprovante divergente", non_retryable=True)
        return receipt

    @activity.defn
    async def reconcile(self, request: dict) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{self.portal_url}/operations/{request['operation_id']}",
                params={"visible": str(request["visible"]).lower()},
            )
            response.raise_for_status()
            result = response.json()
        if result["status"] == "confirmed" and (
            result["receipt"].get("payload_digest") != request["payload_digest"]
            or result["receipt"].get("operation_id") != request["operation_id"]
            or result["receipt"].get("confirmed") is not True
        ):
            return {"status": "unknown"}
        return result

    @activity.defn
    async def record(self, event: dict) -> None:
        with sqlite3.connect(self.journal) as db:
            db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, ?)",
                (f"{event['operation_id']}:{event['stage']}:{digest(event)}", json.dumps(event)),
            )

    def registered(self):
        return [self.propose, self.execute, self.reconcile, self.record]
