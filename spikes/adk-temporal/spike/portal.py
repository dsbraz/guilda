"""Portal HTTP de fixture com efeito persistente e falha após commit."""

import json
import os
import sqlite3

from fastapi import FastAPI, HTTPException

from spike.domain import Move, digest


def create_app(database: str) -> FastAPI:
    app = FastAPI()
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS effects (
                id INTEGER PRIMARY KEY, operation_id TEXT, payload TEXT, receipt TEXT);
            CREATE TABLE IF NOT EXISTS attempts (operation_id TEXT);
        """)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/moves")
    def move(body: dict):
        payload = Move.model_validate(body["payload"]).model_dump(mode="json")
        key = body["operation_id"]
        encoded = json.dumps(payload, sort_keys=True)
        with sqlite3.connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO attempts VALUES (?)", (key,))
            old = db.execute(
                "SELECT payload, receipt FROM effects WHERE operation_id = ?", (key,)
            ).fetchone()
            if old and body["idempotent"]:
                if old[0] != encoded:
                    raise HTTPException(409, "idempotency key reused with different payload")
                return json.loads(old[1])
            receipt = {"operation_id": key, "payload_digest": digest(payload), "confirmed": True}
            db.execute(
                "INSERT INTO effects(operation_id, payload, receipt) VALUES (?, ?, ?)",
                (key, encoded, json.dumps(receipt)),
            )
        # O efeito já foi confirmado; o cliente não recebe seu comprovante.
        if body.get("lose_response") and old is None:
            raise HTTPException(504, "fixture: response lost after commit")
        return receipt

    @app.get("/operations/{key}")
    def operation(key: str, visible: bool = True):
        if not visible:
            return {"status": "unknown"}
        with sqlite3.connect(database) as db:
            row = db.execute(
                "SELECT receipt FROM effects WHERE operation_id = ?", (key,)
            ).fetchone()
        return (
            {"status": "confirmed", "receipt": json.loads(row[0])} if row else {"status": "unknown"}
        )

    @app.get("/stats/{key}")
    def stats(key: str):
        with sqlite3.connect(database) as db:
            return {
                table: db.execute(
                    f"SELECT count(*) FROM {table} WHERE operation_id = ?", (key,)
                ).fetchone()[0]
                for table in ("effects", "attempts")
            }

    return app


def app_factory():
    return create_app(os.environ["SPIKE_PORTAL_DB"])
