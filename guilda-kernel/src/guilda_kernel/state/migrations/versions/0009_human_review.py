"""Approving is always a person's act (PXOS-88).

Delegation lets an agent read and propose with a person's roles, never review: the
database refuses any review whose session actor is not an internal person acting for
themselves, whatever the action.
"""

from alembic import op

revision = "0009"
down_revision = "0008"

STATEMENTS = [
    """
    CREATE FUNCTION pxos.review_policy() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    BEGIN
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
    """
    CREATE TRIGGER review_policy BEFORE INSERT ON pxos.reviews
    FOR EACH ROW EXECUTE FUNCTION pxos.review_policy()
    """,
    "REVOKE ALL ON FUNCTION pxos.review_policy() FROM PUBLIC",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
