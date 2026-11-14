"""Direct execution (PXOS-84): an agent records a fact without review when the policy allows.

The database decides whether the execution stands (autonomy phase, reversibility, module
rules, authorship); a redelivery of the same execution is the same execution.
"""

import asyncpg

from pxos_kernel.identity import Connection
from pxos_kernel.state.canonical import DecisionFinal, Proposal, _translate


async def execute(connection: Connection, proposal: Proposal) -> None:
    """Record the session agent's direct execution of exactly this proposal."""
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO pxos.executions (proposal_id, proposal_digest, executor)"
                " VALUES ($1, $2, '')",
                proposal.id,
                proposal.digest,
            )
    except asyncpg.UniqueViolationError as error:
        if error.constraint_name != "executions_pkey":
            raise _translate(error) from None
        executed = await connection.fetchval(
            "SELECT proposal_digest FROM pxos.executions WHERE proposal_id = $1", proposal.id
        )
        if executed != proposal.digest:
            raise DecisionFinal(f"proposal {proposal.id} was executed differently") from None
    except asyncpg.PostgresError as error:
        raise _translate(error) from None
