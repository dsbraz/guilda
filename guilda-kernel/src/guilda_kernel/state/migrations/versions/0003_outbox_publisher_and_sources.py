"""Outbox publisher role and ingested sources (K52, K54).

- guilda_publisher reads the outbox and marks events published; it sees nothing else;
- event payloads carry identifiers only, so the rejection reason leaves the outbox;
- guilda.sources records each ingested source version once, without its content (the
  text lives in the context memory alone); the application may only insert, so
  replaying a source is a no-op.
"""

from alembic import op

revision = "0003"
down_revision = "0002"

STATEMENTS = [
    """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'guilda_publisher') THEN
            CREATE ROLE guilda_publisher NOLOGIN NOSUPERUSER NOBYPASSRLS;
        END IF;
    END $$
    """,
    "GRANT USAGE ON SCHEMA guilda TO guilda_publisher",
    "GRANT SELECT, UPDATE (published_at) ON guilda.outbox TO guilda_publisher",
    """
    CREATE OR REPLACE FUNCTION guilda.review_record() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = guilda, pg_temp AS $$
    DECLARE
        proposal proposals%ROWTYPE;
    BEGIN
        SELECT * INTO STRICT proposal FROM proposals WHERE id = NEW.proposal_id;
        IF NEW.decision = 'approved' THEN
            INSERT INTO facts (
                entity_id, revision, package, type_name, payload, evidence,
                valid_from, proposal_id
            ) VALUES (
                proposal.entity_id, proposal.base_revision + 1, proposal.package,
                proposal.type_name, proposal.payload, proposal.evidence,
                proposal.valid_from, proposal.id
            );
            INSERT INTO outbox (event_type, entity_id, payload) VALUES (
                'fact.recorded', proposal.entity_id,
                jsonb_build_object(
                    'proposal_id', proposal.id, 'revision', proposal.base_revision + 1,
                    'package', proposal.package, 'type_name', proposal.type_name
                )
            );
        ELSE
            INSERT INTO outbox (event_type, entity_id, payload) VALUES (
                'proposal.rejected', proposal.entity_id,
                jsonb_build_object('proposal_id', proposal.id)
            );
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TABLE guilda.sources (
        source text NOT NULL CHECK (source <> ''),
        version text NOT NULL CHECK (version <> ''),
        ingested_by text NOT NULL DEFAULT guilda.current_actor(),
        ingested_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (source, version)
    )
    """,
    "GRANT INSERT (source, version) ON guilda.sources TO guilda_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
