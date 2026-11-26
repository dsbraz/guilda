"""Only the installation's own logins connect to its database (PXOS-63).

Postgres roles are cluster-wide, and pxos_app is a group role shared by every database
of the cluster. CONNECT is what keeps a login of one installation out of another's
database, so it is revoked from PUBLIC here and granted to each login by
``grant_connect``.
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', "
        "current_database()); END $$"
    )
