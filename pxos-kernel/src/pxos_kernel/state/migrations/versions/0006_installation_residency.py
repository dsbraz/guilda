"""Each installation records the residency it requires (PXOS-72, PRD §6.4).

One row, written by the owner at bootstrap. The application reads it to route model calls
and cannot change it: moving an installation to another residency is a decision for its
operators, not something a session does.
"""

from alembic import op

revision = "0006"
down_revision = "0005"

STATEMENTS = [
    """
    CREATE TABLE pxos.installation (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        residency text NOT NULL CHECK (residency IN ('br', 'global'))
    )
    """,
    "GRANT SELECT ON pxos.installation TO pxos_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
