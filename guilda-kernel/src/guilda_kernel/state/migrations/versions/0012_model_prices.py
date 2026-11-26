"""The installation records the prices of its model, per million tokens (ADR 0006, slice 13).

Prices are deploy configuration, like the route: the bootstrap writes them as the owner,
a new deploy replaces them, and the application reads them to price each run.
"""

from alembic import op

revision = "0012"
down_revision = "0011"

STATEMENTS = [
    "DELETE FROM pxos.installation",
    "ALTER TABLE pxos.installation"
    " ADD COLUMN input_price numeric NOT NULL CHECK (input_price >= 0),"
    " ADD COLUMN output_price numeric NOT NULL CHECK (output_price >= 0)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
