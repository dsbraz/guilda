"""The installation records the route its deploy configured, not a residency label
(PXOS-72, PRD §6.4).

Provider, model and regions are deploy decisions: the bootstrap writes them as the owner,
and a new deploy replaces them. The application reads them to route model calls and still
cannot change them.
"""

from alembic import op

revision = "0008"
down_revision = "0007"

ROUTE = [
    "model",
    "region",
    "memory_model",
    "memory_region",
    "embedding_model",
    "embedding_region",
]

STATEMENTS = [
    "DELETE FROM pxos.installation",
    "ALTER TABLE pxos.installation DROP COLUMN residency",
    "ALTER TABLE pxos.installation"
    " ADD COLUMN provider text NOT NULL CHECK (provider IN ('gemini', 'claude')),"
    + ",".join(f" ADD COLUMN {c} text NOT NULL CHECK (btrim({c}) <> '')" for c in ROUTE),
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
