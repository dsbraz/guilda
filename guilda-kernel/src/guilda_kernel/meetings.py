"""Meeting transcripts port: what a transcription tool delivers to the kernel.

Tools that record per person give one recording for each attendee who recorded, so a
meeting may arrive several times. A transcript source reports each recording with the
identity of its meeting, taken from the calendar event; the kernel keeps one description
per meeting from the most complete recording, and the others as its provenance. The
recording's access tags say who may see it at the source; recall follows them.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import asyncpg

from guilda_kernel.errors import GuildaError
from guilda_kernel.identity import Connection, session
from guilda_kernel.ingestion import Changes, SourceDocument


class RecordingGone(GuildaError):
    """The recording no longer exists at the source, or the source no longer reaches it."""


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
        ``RecordingGone`` when the recording vanished since it was listed, and
        ``ConnectorUnavailable`` when the tool cannot be reached."""
        ...


class MeetingConnector:
    """A transcript source as a connector: one source per meeting, ``meeting:<key>``,
    described by its most complete recording (most seconds; the lowest id on a tie).

    Each delta updates the recordings kept for the source, without content, as ``agent``.
    What to ingest or forget is then read from the recordings kept and from what was
    ingested, never from the delta, so a pass replayed after a failure finds the same work
    left. A meeting whose chosen recording, version or access changed is ingested under a
    new version; one with no recording left is deleted.

    A chosen recording gone by the time its transcript is read is dropped from those kept,
    and the meeting falls back to its next recording in the same pass: syncs may be a cycle
    apart. The drop commits before the cursor moves, so a replay that lists the recording
    again only drops it again.
    """

    def __init__(self, pool: asyncpg.Pool, source: TranscriptSource, *, agent: str) -> None:
        self.name = source.name
        self._pool = pool
        self._source = source
        self._agent = agent

    async def changes(self, cursor: str | None) -> Changes:
        delta = await self._source.changes(cursor)
        async with session(self._pool, self._agent) as connection:
            await _record(connection, self.name, delta)
            chosen = await connection.fetch(_CHOSEN, self.name)
        documents = []
        for row in chosen:
            if (document := await self._chosen_document(row)) is not None:
                documents.append(document)
        async with session(self._pool, self._agent) as connection:
            orphans = await connection.fetch(_ORPHANS, self.name)
        return Changes(
            cursor=delta.cursor,
            documents=tuple(documents),
            deleted=tuple(row["source"] for row in orphans),
        )

    async def _chosen_document(self, row: asyncpg.Record | None) -> SourceDocument | None:
        """The document of the meeting's chosen recording, falling back past those gone;
        None when the one to describe it is retained already or none is left."""
        while row is not None and (version := _version(row)) is not None:
            try:
                return await self._document(row, version)
            except RecordingGone:
                async with session(self._pool, self._agent) as connection:
                    await connection.execute(
                        "DELETE FROM pxos.meeting_recordings WHERE source = $1 AND recording = $2",
                        self.name,
                        row["recording"],
                    )
                    row = await connection.fetchrow(
                        _CHOSEN + " WHERE c.meeting = $2", self.name, row["meeting"]
                    )
        return None

    async def _document(self, row: asyncpg.Record, version: str) -> SourceDocument:
        transcript = await self._source.transcript(row["recording"])
        start = row["start"].astimezone(UTC).isoformat()
        header = f"Meeting: {row['title']}\nStart: {start}\nOrganizer: {row['organizer']}\n\n"
        return SourceDocument(
            source=f"meeting:{row['meeting']}",
            version=version,
            text=header + transcript,
            access=tuple(row["access"]),
        )


async def _record(connection: Connection, source: str, delta: RecordingChanges) -> None:
    await connection.executemany(
        """
        INSERT INTO pxos.meeting_recordings
            (source, recording, meeting, organizer, start, title, version, seconds, access)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (source, recording) DO UPDATE SET meeting = EXCLUDED.meeting,
            organizer = EXCLUDED.organizer, start = EXCLUDED.start, title = EXCLUDED.title,
            version = EXCLUDED.version, seconds = EXCLUDED.seconds, access = EXCLUDED.access
        """,
        [
            (source, r.id, r.meeting.key, r.meeting.organizer, r.meeting.start, r.meeting.title)
            + (r.version, r.seconds, list(r.access))
            for r in delta.recordings
        ],
    )
    await connection.execute(
        "DELETE FROM pxos.meeting_recordings WHERE source = $1 AND recording = ANY($2::text[])",
        source,
        list(delta.removed),
    )


_CHOSEN = """
    SELECT c.*, s.version AS ingested, s.retained
    FROM (
        SELECT DISTINCT ON (meeting) * FROM pxos.meeting_recordings WHERE source = $1
        ORDER BY meeting, seconds DESC, recording COLLATE "C"
    ) c
    LEFT JOIN pxos.source_states s ON s.source = 'meeting:' || c.meeting
"""
"""Each meeting's chosen recording, with what was ingested for the meeting."""

_ORPHANS = """
    SELECT s.source FROM pxos.source_states s
    WHERE s.connector = $1 AND s.state NOT IN ('deleted', 'no_access')
    AND NOT EXISTS (
        SELECT FROM pxos.meeting_recordings r
        WHERE r.source = $1 AND 'meeting:' || r.meeting = s.source
    )
"""
"""The meetings of the source still kept in the context memory with no recording left."""


def _version(row: asyncpg.Record) -> str | None:
    """The version to ingest the chosen recording under; None when it is retained already.

    It names the recording, its version and a digest of its access tags, so new access
    replaces the description's tags, and a count of the descriptions the meeting had:
    going back to a recording ingested before is a new version, since each source version
    is ingested once. One ingested but not retained keeps its version.
    """
    access = hashlib.sha256("\n".join(sorted(row["access"])).encode()).hexdigest()[:12]
    chosen = f"{row['recording']}@{row['version']}@{access}"
    ingested: str | None = row["ingested"]
    if ingested is None:
        return f"{chosen}#1"
    current, _, count = ingested.rpartition("#")
    if current == chosen:
        return None if row["retained"] else ingested
    return f"{chosen}#{int(count) + 1}"
