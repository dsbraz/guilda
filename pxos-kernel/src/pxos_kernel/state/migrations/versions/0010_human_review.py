"""Approving is always a person's act (PXOS-88).

Delegation lets an agent read and propose with a person's roles, never review: the
database refuses any review whose session actor is not an internal person acting for
themselves, whatever the action. Agent autonomy has a single path, its phase plus the
rules of the distribution and the deploy.
"""

from alembic import op

revision = "0010"
down_revision = "0009"

STATEMENTS = [
    """
    CREATE OR REPLACE FUNCTION pxos.review_policy() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    BEGIN
        PERFORM pg_advisory_xact_lock(hashtext(NEW.proposal_id::text));
        IF EXISTS (SELECT FROM executions WHERE proposal_id = NEW.proposal_id) THEN
            RAISE EXCEPTION 'proposal % was already executed', NEW.proposal_id
                USING ERRCODE = 'PX017';
        END IF;
        IF NOT EXISTS (
            SELECT FROM actors WHERE id = current_actor() AND kind = 'internal_person'
              AND id = current_principal()
        ) THEN
            RAISE EXCEPTION 'only a person acting for themselves can review'
                USING ERRCODE = 'PX018';
        END IF;
        RETURN NEW;
    END $$
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
