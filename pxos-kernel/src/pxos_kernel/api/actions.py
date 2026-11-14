"""Action calls through the MCP (PXOS-85): the policy picks the path, the audit records it.

People propose for review as before. For an agent, the policy allows (the fact is
recorded in the same call), asks for approval (the proposal goes to the review queue) or
denies; the database still refuses a direct execution the policy must never allow.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pxos_kernel.api.auth import Caller
from pxos_kernel.identity import Connection
from pxos_kernel.ontology import RegisteredPackage
from pxos_kernel.policy import Verdict, decide
from pxos_kernel.state import AuditResult as Result
from pxos_kernel.state import Evidence, execute, propose, record_audit

PERSON = Verdict("approval", "people propose for review")


async def act(
    connection: Connection,
    registered: RegisteredPackage,
    action_name: str,
    caller: Caller,
    *,
    payload: dict[str, Any],
    evidence: Evidence,
    valid_from: datetime,
    entity_id: UUID | None,
    base_revision: int,
) -> dict[str, Any]:
    package = registered.package
    action = package.action(action_name)
    verdict = PERSON
    if caller.on_behalf_of is not None:
        verdict = decide(
            action,
            readers=package.readers[action.creates],
            principal_roles=await _roles(connection, caller.on_behalf_of),
            autonomy=await connection.fetchval(
                "SELECT autonomy FROM pxos.actors WHERE id = $1", caller.actor
            ),
            payload=payload,
            valid_from=valid_from,
        )
    audit = {"package": package.name, "action": action_name, "reason": verdict.reason}
    if verdict.outcome == "deny":
        await record_audit(connection, **audit, decision="deny", proposal_id=None, result="denied")
        return {"status": "denied", "reason": verdict.reason}
    proposal = await propose(
        connection,
        registered,
        action_name,
        payload=payload,
        evidence=evidence,
        valid_from=valid_from,
        entity_id=entity_id,
        base_revision=base_revision,
    )
    if verdict.outcome == "allow":
        await execute(connection, proposal)
    result: Result = "executed" if verdict.outcome == "allow" else "queued"
    await record_audit(
        connection, **audit, decision=verdict.outcome, proposal_id=proposal.id, result=result
    )
    return {
        "proposal_id": proposal.id,
        "entity_id": proposal.entity_id,
        "digest": proposal.digest,
        "status": "executed" if result == "executed" else "pending",
        "reason": verdict.reason,
        "approver_role": action.approver,
    }


async def _roles(connection: Connection, actor: str) -> set[str]:
    rows = await connection.fetch("SELECT role FROM pxos.actor_roles WHERE actor_id = $1", actor)
    return {row["role"] for row in rows}
