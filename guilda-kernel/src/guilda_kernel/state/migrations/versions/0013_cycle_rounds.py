"""Rounds of an agent's cycle, recorded once (ADR 0006 §2, slice 13).

- a run of a cycle's round keeps its cycle, its round, what it answered, the model and the
  tokens it used and their cost. The database runs each round of an agent's cycle once,
  so a retry finds the answer instead of asking the model again;
- a proposal whose evidence is an agent run is that run's deliverable: one per run, so a
  round delivers once however often it is retried;
- an agent's post may carry a run's questions: one post per run, so a retry posts nothing
  twice. Only that column is added to what the application may insert.
"""

from alembic import op

revision = "0013"
down_revision = "0012"

STATEMENTS = [
    """
    ALTER TABLE guilda.agent_runs
        ADD COLUMN cycle text CHECK (cycle <> ''),
        ADD COLUMN round integer CHECK (round >= 1),
        ADD COLUMN output jsonb,
        ADD COLUMN model text,
        ADD COLUMN input_tokens integer CHECK (input_tokens >= 0),
        ADD COLUMN output_tokens integer CHECK (output_tokens >= 0),
        ADD COLUMN cost numeric CHECK (cost >= 0),
        ADD CHECK ((cycle IS NULL) = (round IS NULL)),
        ADD UNIQUE (agent, cycle, round)
    """,
    """
    CREATE UNIQUE INDEX proposals_one_per_run ON guilda.proposals ((evidence->>'source'))
    WHERE evidence->>'source' LIKE 'agent-run:%'
    """,
    "ALTER TABLE guilda.messages ADD COLUMN run uuid UNIQUE REFERENCES guilda.agent_runs",
    "GRANT INSERT (run) ON guilda.messages TO guilda_app",
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
