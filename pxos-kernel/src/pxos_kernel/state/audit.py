"""Action audit (PXOS-85, PRD §6.3): who asked, what, the policy decision and the result.

The proposal is referenced, never copied, so deleting personal data stays effective; the
approval comes from its review. The database sets the actor and principal from the
session, and the application can only append.
"""

from typing import Literal
from uuid import UUID

from pxos_kernel.identity import Connection

Result = Literal["executed", "queued", "denied"]


async def record_audit(
    connection: Connection,
    *,
    package: str,
    action: str,
    decision: Literal["allow", "approval", "deny"],
    reason: str,
    proposal_id: UUID | None,
    result: Result,
) -> None:
    await connection.execute(
        """
        INSERT INTO pxos.action_audit (actor, principal, package, action, decision, reason,
                                       proposal_id, result)
        VALUES ('', '', $1, $2, $3, $4, $5, $6)
        """,
        package,
        action,
        decision,
        reason,
        proposal_id,
        result,
    )
