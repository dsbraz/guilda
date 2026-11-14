"""Outbox handlers that nudge the workflow waiting for a decision (ADR 0004 §2, rule 5)."""

from uuid import UUID

from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from pxos_kernel.state import Event, Handler
from pxos_kernel.workflows.definitions import AwaitReview


def review_workflow_id(proposal_id: UUID | str) -> str:
    return f"pxos-review-{proposal_id}"


def review_signals(client: Client) -> dict[str, Handler]:
    """Handlers for the decision events. A proposal nobody waits for is ignored, so it
    never blocks the outbox; any other failure leaves the event for the next run."""

    async def nudge(event: Event) -> None:
        handle = client.get_workflow_handle(review_workflow_id(event.payload["proposal_id"]))
        try:
            await handle.signal(AwaitReview.reviewed)
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise

    return {"fact.recorded": nudge, "proposal.rejected": nudge}
