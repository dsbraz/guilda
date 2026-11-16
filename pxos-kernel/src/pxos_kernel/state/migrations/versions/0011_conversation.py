"""Conversation on a channel (ADR 0006, slice 11).

- pxos.channel_identities binds an identity on a channel (a user id there) to one actor;
  the owner writes it at bootstrap and the application only reads it.
"""

from alembic import op

revision = "0011"
down_revision = "0009"

STATEMENTS = [
    """
    CREATE TABLE pxos.channel_identities (
        channel_id text PRIMARY KEY CHECK (channel_id <> ''),
        actor_id text NOT NULL REFERENCES pxos.actors
    )
    """,
    "GRANT SELECT ON pxos.channel_identities TO pxos_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
