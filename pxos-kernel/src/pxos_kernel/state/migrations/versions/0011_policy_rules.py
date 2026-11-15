"""Policy rules belong to the installation, not to the package (PXOS-87).

The distribution declares the rules at build time and the deploy may replace them; the
bootstrap writes them as the owner and the application only reads them. They are not
bound to the package digest: changing a limit does not invalidate open proposals, and a
direct execution is checked against the rules in force. Rule types: ``max`` and
``within_days`` on a payload parameter, ``roles`` on whom the agent acts for.
"""

from alembic import op

revision = "0011"
down_revision = "0010"

STATEMENTS = [
    "DROP FUNCTION pxos.within_rules(pxos.proposals)",
    "DROP TABLE pxos.package_rules",
    """
    CREATE TABLE pxos.policy_rules (
        package text NOT NULL,
        action text NOT NULL,
        position integer NOT NULL CHECK (position >= 0),
        kind text NOT NULL CHECK (kind IN ('max', 'within_days', 'roles')),
        effect text NOT NULL CHECK (effect IN ('approval', 'deny')),
        parameter text,
        value numeric CHECK (value >= 0),
        roles text[],
        PRIMARY KEY (package, action, position),
        CHECK (
            (kind = 'roles' AND parameter IS NULL AND value IS NULL AND cardinality(roles) > 0)
            OR (kind <> 'roles' AND parameter IS NOT NULL AND value IS NOT NULL
                AND coalesce(cardinality(roles), 0) = 0)
        )
    )
    """,
    # True when the call respects every rule in force for the action.
    """
    CREATE FUNCTION pxos.within_rules(proposal pxos.proposals) RETURNS boolean
    LANGUAGE sql STABLE SET search_path = pxos, pg_temp AS $$
        SELECT coalesce(bool_and(coalesce(CASE r.kind
            WHEN 'max' THEN (proposal.payload->>r.parameter)::numeric <= r.value
            WHEN 'within_days' THEN (proposal.payload->>r.parameter)::date
                 <= (proposal.valid_from AT TIME ZONE 'UTC')::date + r.value::integer
            ELSE EXISTS (
                SELECT FROM actor_roles
                WHERE actor_id = current_principal() AND role = ANY (r.roles)
            )
        END, false)), true)
        FROM policy_rules r
        WHERE r.package = proposal.package AND r.action = proposal.action
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION pxos.execution_guard() RETURNS trigger
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
            SELECT FROM policy_rules
            WHERE package = proposal.package AND action = proposal.action
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
    "REVOKE ALL ON FUNCTION pxos.within_rules(pxos.proposals) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION pxos.within_rules(pxos.proposals) TO pxos_app",
    "GRANT SELECT ON pxos.policy_rules TO pxos_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
