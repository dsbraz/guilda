"""Each agent run records the version it ran (K79, K34; ADR 0004 §3).

The run is written as the agent, in the transaction that ingests the source and creates
the proposals, so a run and what it proposed commit together. The database sets the
agent from the session and refuses a version that is not one of that agent's approved
definitions. The application cannot change or remove a run.
"""

from alembic import op

revision = "0007"
down_revision = "0006"

STATEMENTS = [
    """
    CREATE TABLE guilda.agent_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        agent text NOT NULL,
        definition uuid NOT NULL,
        revision integer NOT NULL,
        context_ref text NOT NULL,
        started_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (definition, revision) REFERENCES guilda.facts (entity_id, revision)
    )
    """,
    """
    CREATE FUNCTION guilda.agent_run_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = guilda, pg_temp AS $$
    BEGIN
        NEW.agent := current_actor();
        NEW.started_at := now();
        IF NOT EXISTS (
            SELECT FROM facts
            WHERE entity_id = NEW.definition AND revision = NEW.revision
              AND package = 'guilda_agents' AND type_name = 'AgentVersion'
              AND payload->>'agent' = NEW.agent
        ) THEN
            RAISE EXCEPTION 'version %/% is not a definition of %',
                NEW.definition, NEW.revision, NEW.agent
                USING ERRCODE = 'PX015';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER agent_run_guard BEFORE INSERT ON guilda.agent_runs
    FOR EACH ROW EXECUTE FUNCTION guilda.agent_run_guard()
    """,
    "GRANT SELECT, INSERT ON guilda.agent_runs TO guilda_app",
    "ALTER TABLE guilda.agent_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE guilda.agent_runs FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY agent_runs_read ON guilda.agent_runs FOR SELECT USING (
        agent = guilda.current_actor() OR guilda.has_role('platform-admin')
    )
    """,
    "CREATE POLICY agent_runs_insert ON guilda.agent_runs FOR INSERT WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
