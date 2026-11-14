"""Each agent run records the version it ran (PXOS-79, K34; ADR 0004 §3).

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
    CREATE TABLE pxos.agent_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        agent text NOT NULL,
        definition uuid NOT NULL,
        revision integer NOT NULL,
        context_ref text NOT NULL,
        started_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (definition, revision) REFERENCES pxos.facts (entity_id, revision)
    )
    """,
    """
    CREATE FUNCTION pxos.agent_run_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    BEGIN
        NEW.agent := current_actor();
        NEW.started_at := now();
        IF NOT EXISTS (
            SELECT FROM facts
            WHERE entity_id = NEW.definition AND revision = NEW.revision
              AND package = 'pxos_agents' AND type_name = 'AgentVersion'
              AND operation = 'assert' AND payload->>'agent' = NEW.agent
        ) THEN
            RAISE EXCEPTION 'version %/% is not a definition of %',
                NEW.definition, NEW.revision, NEW.agent
                USING ERRCODE = 'PX015';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER agent_run_guard BEFORE INSERT ON pxos.agent_runs
    FOR EACH ROW EXECUTE FUNCTION pxos.agent_run_guard()
    """,
    "GRANT SELECT, INSERT ON pxos.agent_runs TO pxos_app",
    "ALTER TABLE pxos.agent_runs ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pxos.agent_runs FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY agent_runs_read ON pxos.agent_runs FOR SELECT USING (
        agent = pxos.current_actor() OR pxos.has_role('platform-admin')
    )
    """,
    "CREATE POLICY agent_runs_insert ON pxos.agent_runs FOR INSERT WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
