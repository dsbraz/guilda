"""External actors reach no internal context (K61).

Customers and suppliers have their own identity (``external_person``). Every read and
write of the canonical tables goes through ``current_actor()``, so refusing there a
session whose actor or principal is external refuses it everywhere, whatever roles the
external actor holds. External actors will come in through channels (K23).
"""

from alembic import op

revision = "0005"
down_revision = "0004"

STATEMENTS = [
    "ALTER TABLE guilda.actors DROP CONSTRAINT actors_kind_check",
    "ALTER TABLE guilda.actors ADD CONSTRAINT actors_kind_check "
    "CHECK (kind IN ('internal_person', 'external_person', 'agent'))",
    """
    CREATE OR REPLACE FUNCTION guilda.current_actor() RETURNS text
    LANGUAGE plpgsql STABLE SET search_path = guilda, pg_temp AS $$
    DECLARE
        actor text := nullif(current_setting('guilda.actor', true), '');
        principal text := nullif(current_setting('guilda.on_behalf_of', true), '');
        external text;
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
        SELECT id INTO external FROM actors
        WHERE id IN (actor, principal) AND kind = 'external_person' LIMIT 1;
        IF external IS NOT NULL THEN
            RAISE EXCEPTION '% is external and has no access to internal context', external
                USING ERRCODE = 'PX003';
        END IF;
        RETURN actor;
    END $$
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
