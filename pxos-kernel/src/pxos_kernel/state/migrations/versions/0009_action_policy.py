"""Action with policy (PXOS-82 to PXOS-85, PRD §6.3): direct execution, autonomy and audit.

The policy decides in the application, and the database refuses what it must never allow
(ADR 0003 §2):
- an agent writes a fact without review only through ``executions``: it must be the
  author of the proposal, the action reversible, its autonomy phase 2 or 3 (phase 2 only
  when the module declares rules) and the payload within every declared rule;
- a proposal is decided once: a review after an execution, or the reverse, is refused;
- an irreversible action is approved only by the person themselves, never by an agent
  acting under delegation;
- a direct execution records the fact and drops the unpublished approval card event;
- every action call leaves an append-only audit row; its actor comes from the session.
Autonomy is per agent and written by the owner at bootstrap; the application reads it.
"""

from alembic import op

revision = "0009"
down_revision = "0008"

STATEMENTS = [
    "ALTER TABLE pxos.actors ADD COLUMN autonomy smallint NOT NULL DEFAULT 1"
    " CHECK (autonomy BETWEEN 1 AND 3), ADD CHECK (kind = 'agent' OR autonomy = 1)",
    "ALTER TABLE pxos.package_actions ADD COLUMN reversible boolean NOT NULL DEFAULT false",
    """
    CREATE TABLE pxos.package_rules (
        package text NOT NULL,
        digest text NOT NULL,
        action text NOT NULL,
        parameter text NOT NULL,
        operator text NOT NULL CHECK (operator IN ('max', 'within_days')),
        value numeric NOT NULL CHECK (value >= 0),
        PRIMARY KEY (package, digest, action, parameter)
    )
    """,
    """
    CREATE TABLE pxos.executions (
        proposal_id uuid PRIMARY KEY REFERENCES pxos.proposals,
        proposal_digest text NOT NULL,
        executor text NOT NULL REFERENCES pxos.actors,
        executed_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE pxos.action_audit (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        actor text NOT NULL REFERENCES pxos.actors,
        principal text NOT NULL REFERENCES pxos.actors,
        package text NOT NULL,
        action text NOT NULL,
        decision text NOT NULL CHECK (decision IN ('allow', 'approval', 'deny')),
        reason text NOT NULL CHECK (btrim(reason) <> ''),
        proposal_id uuid REFERENCES pxos.proposals,
        result text NOT NULL CHECK (result IN ('executed', 'queued', 'denied')),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((decision, result) IN (('allow', 'executed'), ('approval', 'queued'),
                                      ('deny', 'denied'))),
        CHECK ((proposal_id IS NULL) = (decision = 'deny'))
    )
    """,
    # True when the payload respects every rule the module declares for the action.
    """
    CREATE FUNCTION pxos.within_rules(proposal pxos.proposals) RETURNS boolean
    LANGUAGE sql STABLE SET search_path = pxos, pg_temp AS $$
        SELECT coalesce(bool_and(coalesce(CASE r.operator
            WHEN 'max' THEN (proposal.payload->>r.parameter)::numeric <= r.value
            ELSE (proposal.payload->>r.parameter)::date
                 <= (proposal.valid_from AT TIME ZONE 'UTC')::date + r.value::integer
        END, false)), true)
        FROM package_rules r
        WHERE r.package = proposal.package AND r.digest = proposal.package_digest
          AND r.action = proposal.action
    $$
    """,
    """
    CREATE FUNCTION pxos.execution_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    DECLARE
        proposal proposals%ROWTYPE;
        executor actors%ROWTYPE;
        reversible boolean;
        ruled boolean;
    BEGIN
        NEW.executor := current_actor();
        NEW.executed_at := now();
        PERFORM pg_advisory_xact_lock(hashtext(NEW.proposal_id::text));
        SELECT * INTO proposal FROM proposals WHERE id = NEW.proposal_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'executor cannot read proposal %', NEW.proposal_id
                USING ERRCODE = 'PX016';
        END IF;
        IF NEW.proposal_digest <> proposal.digest THEN
            RAISE EXCEPTION 'executed digest does not match the proposal'
                USING ERRCODE = 'PX010';
        END IF;
        IF EXISTS (SELECT FROM reviews WHERE proposal_id = proposal.id) THEN
            RAISE EXCEPTION 'proposal % was already reviewed', proposal.id
                USING ERRCODE = 'PX017';
        END IF;
        SELECT * INTO STRICT executor FROM actors WHERE id = NEW.executor;
        IF executor.kind <> 'agent' OR proposal.author <> executor.id THEN
            RAISE EXCEPTION 'only the agent that proposed can execute directly'
                USING ERRCODE = 'PX016';
        END IF;
        SELECT a.reversible INTO reversible FROM package_actions a
        JOIN packages p ON p.name = a.package AND p.digest = a.digest
        WHERE a.package = proposal.package AND a.digest = proposal.package_digest
          AND a.action = proposal.action;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'package % changed after the proposal', proposal.package
                USING ERRCODE = 'PX012';
        END IF;
        IF NOT reversible THEN
            RAISE EXCEPTION 'action % is irreversible and needs a person', proposal.action
                USING ERRCODE = 'PX016';
        END IF;
        ruled := EXISTS (
            SELECT FROM package_rules WHERE package = proposal.package
              AND digest = proposal.package_digest AND action = proposal.action
        );
        IF executor.autonomy < 2 OR (executor.autonomy = 2 AND NOT ruled) THEN
            RAISE EXCEPTION 'autonomy phase % of % does not allow %',
                executor.autonomy, executor.id, proposal.action
                USING ERRCODE = 'PX016';
        END IF;
        IF NOT within_rules(proposal) THEN
            RAISE EXCEPTION 'proposal % is outside the rules of %', proposal.id, proposal.action
                USING ERRCODE = 'PX016';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER execution_guard BEFORE INSERT ON pxos.executions
    FOR EACH ROW EXECUTE FUNCTION pxos.execution_guard()
    """,
    """
    CREATE FUNCTION pxos.execution_record() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path = pxos, pg_temp AS $$
    DECLARE
        proposal proposals%ROWTYPE;
    BEGIN
        SELECT * INTO STRICT proposal FROM proposals WHERE id = NEW.proposal_id;
        INSERT INTO facts (
            entity_id, revision, package, type_name, operation, payload, evidence,
            valid_from, valid_to, proposal_id
        ) VALUES (
            proposal.entity_id, proposal.base_revision + 1, proposal.package,
            proposal.type_name, proposal.operation, proposal.payload, proposal.evidence,
            proposal.valid_from, proposal.valid_to, proposal.id
        );
        DELETE FROM outbox WHERE event_type = 'proposal.created' AND published_at IS NULL
          AND payload->>'proposal_id' = proposal.id::text;
        INSERT INTO outbox (event_type, entity_id, payload) VALUES (
            'fact.recorded', proposal.entity_id,
            jsonb_build_object(
                'proposal_id', proposal.id, 'revision', proposal.base_revision + 1,
                'package', proposal.package, 'type_name', proposal.type_name,
                'operation', proposal.operation
            )
        );
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER execution_record AFTER INSERT ON pxos.executions
    FOR EACH ROW EXECUTE FUNCTION pxos.execution_record()
    """,
    # Reviews: decided once, and an irreversible action only by the person themselves.
    """
    CREATE FUNCTION pxos.review_policy() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    DECLARE
        proposal proposals%ROWTYPE;
    BEGIN
        PERFORM pg_advisory_xact_lock(hashtext(NEW.proposal_id::text));
        SELECT * INTO proposal FROM proposals WHERE id = NEW.proposal_id;
        IF NOT FOUND THEN
            RETURN NEW;
        END IF;
        IF EXISTS (SELECT FROM executions WHERE proposal_id = proposal.id) THEN
            RAISE EXCEPTION 'proposal % was already executed', proposal.id
                USING ERRCODE = 'PX017';
        END IF;
        IF EXISTS (
            SELECT FROM package_actions
            WHERE package = proposal.package AND digest = proposal.package_digest
              AND action = proposal.action AND NOT reversible
        ) AND NOT EXISTS (
            SELECT FROM actors WHERE id = current_actor() AND kind = 'internal_person'
              AND id = current_principal()
        ) THEN
            RAISE EXCEPTION 'irreversible action % needs the review of a person', proposal.action
                USING ERRCODE = 'PX018';
        END IF;
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER review_policy BEFORE INSERT ON pxos.reviews
    FOR EACH ROW EXECUTE FUNCTION pxos.review_policy()
    """,
    """
    CREATE FUNCTION pxos.audit_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path = pxos, pg_temp AS $$
    BEGIN
        NEW.actor := current_actor();
        NEW.principal := current_principal();
        NEW.recorded_at := now();
        RETURN NEW;
    END $$
    """,
    """
    CREATE TRIGGER audit_guard BEFORE INSERT ON pxos.action_audit
    FOR EACH ROW EXECUTE FUNCTION pxos.audit_guard()
    """,
    "REVOKE ALL ON FUNCTION pxos.within_rules(pxos.proposals), pxos.execution_guard(),"
    " pxos.execution_record(), pxos.review_policy(), pxos.audit_guard() FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION pxos.within_rules(pxos.proposals) TO pxos_app",
    "GRANT SELECT ON pxos.package_rules TO pxos_app",
    "GRANT SELECT, INSERT ON pxos.executions, pxos.action_audit TO pxos_app",
    "ALTER TABLE pxos.executions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pxos.executions FORCE ROW LEVEL SECURITY",
    "ALTER TABLE pxos.action_audit ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE pxos.action_audit FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY executions_read ON pxos.executions FOR SELECT USING (
        EXISTS (SELECT FROM pxos.proposals p WHERE p.id = proposal_id)
    )
    """,
    "CREATE POLICY executions_insert ON pxos.executions FOR INSERT WITH CHECK (true)",
    """
    CREATE POLICY audit_read ON pxos.action_audit FOR SELECT USING (
        pxos.current_principal() IN (actor, principal)
    )
    """,
    "CREATE POLICY audit_insert ON pxos.action_audit FOR INSERT WITH CHECK (true)",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
