"""Granola's real public API, opt-in: runs only when GUILDA_GRANOLA_API_KEY holds a workspace
key (GUILDA_GRANOLA_SPACE_ID or GUILDA_GRANOLA_FOLDER_ID narrow it). It reads, never writes."""

import os

import pytest

from guilda_granola import GranolaTranscripts

KEY = os.environ.get("GUILDA_GRANOLA_API_KEY")
pytestmark = pytest.mark.skipif(not KEY, reason="GUILDA_GRANOLA_API_KEY not set")


async def test_the_workspace_notes_are_recordings_with_their_transcripts() -> None:
    source = GranolaTranscripts(
        KEY or "",
        access=("corporate",),
        space_id=os.environ.get("GUILDA_GRANOLA_SPACE_ID"),
        folder_id=os.environ.get("GUILDA_GRANOLA_FOLDER_ID"),
    )

    changes = await source.changes(None)

    assert all(r.seconds >= 0 and r.meeting.organizer for r in changes.recordings)
    if changes.recordings:
        assert changes.cursor
        assert isinstance(await source.transcript(changes.recordings[0].id), str)
        again = await source.changes(changes.cursor)
        assert {r.id for r in again.recordings} <= {r.id for r in changes.recordings}
