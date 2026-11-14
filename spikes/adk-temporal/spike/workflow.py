"""Temporal é o único dono do ciclo durável. Nenhuma chamada ADK no replay."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from spike.domain import digest


@workflow.defn
class AllocationWorkflow:
    def __init__(self):
        self.state = {"stage": "starting"}
        self.decision = None
        self.clarification = None
        self.approver = ""

    @workflow.query
    def status(self) -> dict:
        return self.state

    @workflow.update
    async def clarify(self, answer: str) -> None:
        if self.state["stage"] != "needs_clarification" or not answer.strip():
            raise ApplicationError("não há esclarecimento pendente")
        self.clarification = answer
        self.state["stage"] = "clarification_received"

    @workflow.update
    async def decide(self, decision: dict) -> None:
        # Identidade é fixture confiável no cliente do spike, não autenticação de produto.
        if self.state["stage"] != "awaiting_approval":
            raise ApplicationError("não há aprovação pendente")
        if decision.get("actor") != self.approver:
            raise ApplicationError("papel não autorizado na fixture")
        if decision.get("digest") != self.state["digest"]:
            raise ApplicationError("aprovação não corresponde à proposta")
        if decision.get("decision") not in ("approved", "rejected"):
            raise ApplicationError("decisão inválida")
        if set(decision) != {"actor", "digest", "decision"}:
            raise ApplicationError("aprovação não pode substituir parâmetros")
        self.decision = decision
        self.state["stage"] = "decision_received"

    async def record(self, stage: str, **details):
        await workflow.execute_activity(
            "record",
            {"operation_id": workflow.info().workflow_id, "stage": stage, **details},
            start_to_close_timeout=timedelta(seconds=10),
        )

    @workflow.run
    async def run(self, request: dict) -> dict:
        self.approver = request["approver"]
        text = request["text"]
        while True:
            proposal = await workflow.execute_activity(
                "propose",
                text,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            if proposal["status"] == "proposed":
                break
            self.clarification = None
            self.state = {"stage": "needs_clarification", "question": proposal["question"]}
            await self.record("needs_clarification", proposal=proposal)
            await workflow.wait_condition(lambda: self.clarification is not None)
            text += "\nEsclarecimento: " + self.clarification

        envelope = {
            "operation_id": workflow.info().workflow_id,
            "action": "move_allocation",
            "payload": proposal["move"],
            "evidence": proposal["evidence"],
            "approver": self.approver,
        }
        approved_digest = digest(envelope)
        self.state = {
            "stage": "awaiting_approval",
            "envelope": envelope,
            "digest": approved_digest,
            "tools_used": proposal["tools_used"],
        }
        await self.record("proposed", envelope=envelope)
        await workflow.wait_condition(lambda: self.decision is not None)
        await self.record(self.decision["decision"], decision=self.decision)
        if self.decision["decision"] == "rejected":
            self.state["stage"] = "rejected"
            return self.state

        self.state["stage"] = "executing"
        command = {
            "envelope": envelope,
            "approved_digest": approved_digest,
            "idempotent": request["idempotent"],
            "lose_response": request["lose_response"],
        }
        try:
            receipt = await workflow.execute_activity(
                "execute",
                command,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(milliseconds=100),
                    maximum_attempts=3 if request["idempotent"] else 1,
                ),
            )
        except ActivityError:
            reconciliation = await workflow.execute_activity(
                "reconcile",
                {
                    "operation_id": workflow.info().workflow_id,
                    "payload_digest": digest(envelope["payload"]),
                    "visible": request["reconcile_visible"],
                },
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            if reconciliation["status"] != "confirmed":
                self.state["stage"] = "reconciliation_required"
                await self.record("reconciliation_required", envelope=envelope)
                return self.state
            receipt = reconciliation["receipt"]

        await self.record("confirmed", receipt=receipt, envelope=envelope)
        self.state.update(stage="confirmed", receipt=receipt)
        return self.state
