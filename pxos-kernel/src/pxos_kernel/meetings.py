"""Meeting transcripts port: what a transcription tool delivers to the kernel.

Tools that record per person give one recording for each attendee who recorded, so a
meeting may arrive several times. A transcript source reports each recording with the
identity of its meeting, taken from the calendar event; the kernel keeps one description
per meeting from the most complete recording, and the others as its provenance. The
recording's access tags say who may see it at the source; recall follows them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class Meeting:
    """A meeting as its calendar event identifies it, the same for every attendee."""

    organizer: str
    start: datetime
    title: str

    @property
    def key(self) -> str:
        start = self.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
        return f"{self.organizer.strip().lower()}|{start}|{' '.join(self.title.split())}"


@dataclass(frozen=True)
class Recording:
    id: str
    """The tool's id of this recording, unique within the source."""
    meeting: Meeting
    version: str
    """Changes whenever the recording's transcript changes."""
    seconds: float
    """How much of the meeting the transcript covers; the most complete recording wins."""
    access: tuple[str, ...]


@dataclass(frozen=True)
class RecordingChanges:
    """A source's delta since a cursor: new or changed recordings, the ids of those deleted
    or no longer accessible, and the cursor after them."""

    cursor: str
    recordings: tuple[Recording, ...] = ()
    removed: tuple[str, ...] = ()


class TranscriptSource(Protocol):
    name: str

    async def changes(self, cursor: str | None) -> RecordingChanges:
        """The delta since ``cursor`` (from the start when None); raises
        ``ConnectorUnavailable`` when the tool cannot be reached."""
        ...

    async def transcript(self, recording: str) -> str:
        """The transcript of ``recording`` as text, with its speakers; raises
        ``ConnectorUnavailable`` when the tool cannot be reached."""
        ...
