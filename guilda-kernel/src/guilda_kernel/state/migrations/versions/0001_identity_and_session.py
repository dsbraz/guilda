"""Application role, identities and the session actor (K50, K51).

The application connects with a login that is a member of ``guilda_app``: no ownership,
no superuser, no BYPASSRLS. Identity tables are written only by the owner at bootstrap.
"""

from alembic import op

revision = "0001"
down_revision = None

STATEMENTS = [
    """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'guilda_app') THEN
            CREATE ROLE guilda_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
        END IF;
    END $$
    """,
    "CREATE SCHEMA guilda",
    "REVOKE ALL ON SCHEMA guilda FROM PUBLIC",
    "GRANT USAGE ON SCHEMA guilda TO guilda_app",
    """
    CREATE TABLE guilda.actors (
        id text PRIMARY KEY CHECK (id ~ '^[a-z][a-z0-9.-]*$'),
        kind text NOT NULL CHECK (kind IN ('internal_person', 'agent')),
        name text NOT NULL
    )
    """,
    """
    CREATE TABLE guilda.actor_roles (
        actor_id text NOT NULL REFERENCES guilda.actors,
        role text NOT NULL CHECK (role ~ '^[a-z][a-z0-9-]*$'),
        PRIMARY KEY (actor_id, role)
    )
    """,
    """
    CREATE TABLE guilda.delegations (
        agent_id text NOT NULL REFERENCES guilda.actors,
        person_id text NOT NULL REFERENCES guilda.actors,
        PRIMARY KEY (agent_id, person_id)
    )
    """,
    "GRANT SELECT ON guilda.actors, guilda.actor_roles, guilda.delegations TO guilda_app",
    # The actor of the transaction, set by the kernel session with set_config(..., true).
    # A pooled connection returns '' after a previous transaction, so empty means none.
    """
    CREATE FUNCTION guilda.current_actor() RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = guilda, pg_temp AS $$
    DECLARE
        actor text := nullif(current_setting('guilda.actor', true), '');
        principal text := nullif(current_setting('guilda.on_behalf_of', true), '');
    BEGIN
        IF actor IS NULL THEN
            RAISE EXCEPTION 'no actor in session' USING ERRCODE = 'PX001';
        END IF;
        IF NOT EXISTS (SELECT FROM actors WHERE id = actor) THEN
            RAISE EXCEPTION 'unknown actor %', actor USING ERRCODE = 'PX001';
        END IF;
        IF principal IS NOT NULL AND NOT EXISTS (
            SELECT FROM delegations WHERE agent_id = actor AND person_id = principal
        ) THEN
            RAISE EXCEPTION '% has no delegation from %', actor, principal
                USING ERRCODE = 'PX002';
        END IF;
        RETURN actor;
    END $$
    """,
    # Whose permissions apply: the delegating person for an agent acting on their behalf.
    """
    CREATE FUNCTION guilda.current_principal() RETURNS text
    LANGUAGE sql STABLE SET search_path = guilda, pg_temp AS $$
        SELECT coalesce(nullif(current_setting('guilda.on_behalf_of', true), ''), current_actor())
        FROM (SELECT current_actor()) AS checked
    $$
    """,
    """
    CREATE FUNCTION guilda.has_role(wanted text) RETURNS boolean
    LANGUAGE sql STABLE SET search_path = guilda, pg_temp AS $$
        SELECT EXISTS (
            SELECT FROM actor_roles WHERE actor_id = current_principal() AND role = wanted
        )
    $$
    """,
    "REVOKE ALL ON FUNCTION guilda.current_actor(), guilda.current_principal(), "
    "guilda.has_role(text) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION guilda.current_actor(), guilda.current_principal(), "
    "guilda.has_role(text) TO guilda_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
