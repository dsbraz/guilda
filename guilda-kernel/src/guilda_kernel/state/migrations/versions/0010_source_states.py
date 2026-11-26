"""Source states and connector cursors (ADR 0006 §1, slice 10).

- guilda.source_states keeps, per source, its connector, the latest version ingested,
  whether the context memory retained it, and its state: available, unavailable (the
  connector could not be reached), deleted or no_access (both forgotten). A version
  committed but not retained is pending, so a failure to retain loses no source;
- guilda.connector_cursors keeps where each connector's next delta starts;
- neither holds content, and the application can read and write them but not delete.
"""

from alembic import op

revision = "0010"
down_revision = "0009"

STATEMENTS = [
    """
    CREATE TABLE guilda.source_states (
        source text PRIMARY KEY CHECK (source <> ''),
        connector text NOT NULL CHECK (connector <> ''),
        version text,
        state text NOT NULL CHECK (state IN ('available', 'unavailable', 'deleted', 'no_access')),
        retained boolean NOT NULL DEFAULT false
    )
    """,
    "GRANT SELECT, INSERT, UPDATE ON guilda.source_states TO guilda_app",
    """
    CREATE TABLE guilda.connector_cursors (
        connector text PRIMARY KEY CHECK (connector <> ''),
        cursor text NOT NULL
    )
    """,
    "GRANT SELECT, INSERT, UPDATE ON guilda.connector_cursors TO guilda_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
