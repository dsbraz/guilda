"""Recordings of meetings, one description per meeting (meeting transcripts port).

- guilda.meeting_recordings keeps each recording a transcript source reported: its
  meeting's key and calendar identity, its version, how many seconds it covers and its
  access tags. Never its transcript: that goes to the context memory only;
- a source's recording is unique; a recording removed at the source is deleted here, so
  the remaining recordings of a meeting are those the source still has;
- only agents reach it, under RLS: an agent's session reads and writes any source's
  recordings; a person's reads and writes none, and without a session it is refused.
"""

from alembic import op

revision = "0014"
down_revision = "0013"

STATEMENTS = [
    """
    CREATE TABLE guilda.meeting_recordings (
        source text NOT NULL CHECK (source <> ''),
        recording text NOT NULL CHECK (recording <> ''),
        meeting text NOT NULL CHECK (meeting <> ''),
        organizer text NOT NULL,
        start timestamptz NOT NULL,
        title text NOT NULL,
        version text NOT NULL,
        seconds double precision NOT NULL CHECK (seconds >= 0),
        access text[] NOT NULL,
        PRIMARY KEY (source, recording)
    )
    """,
    "CREATE INDEX meeting_recordings_meeting ON guilda.meeting_recordings (source, meeting)",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON guilda.meeting_recordings TO guilda_app",
    "ALTER TABLE guilda.meeting_recordings ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE guilda.meeting_recordings FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY meeting_recordings_agents ON guilda.meeting_recordings
    USING (EXISTS (SELECT FROM guilda.actors WHERE id = guilda.current_actor() AND kind = 'agent'))
    WITH CHECK (
        EXISTS (SELECT FROM guilda.actors WHERE id = guilda.current_actor() AND kind = 'agent')
    )
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)
