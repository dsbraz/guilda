from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, MEMORY_INSTRUCTIONS

from db_testing import Database
from fakes_testing import FakeContextStore, FakeTranscriptSource
from guilda_kernel.identity import session
from guilda_kernel.ingestion import ConnectorUnavailable, sync
from guilda_kernel.meetings import Meeting, MeetingConnector, Recording, RecordingGone


def test_attendees_recording_the_same_meeting_name_it_the_same_way() -> None:
    brt = timezone(timedelta(hours=-3))
    one = Meeting("PM@Example.com ", datetime(2026, 11, 16, 9, 0, 0, tzinfo=brt), "Weekly  piloto")
    other = Meeting(
        "pm@example.com", datetime(2026, 11, 16, 12, 0, 30, tzinfo=UTC), "Weekly piloto"
    )
    assert one.key == other.key == "pm@example.com|2026-11-16T12:00Z|Weekly piloto"


def test_another_occurrence_of_a_series_is_another_meeting() -> None:
    monday = Meeting("pm@example.com", datetime(2026, 11, 16, 12, 0, tzinfo=UTC), "Weekly")
    next_monday = Meeting("pm@example.com", datetime(2026, 11, 23, 12, 0, tzinfo=UTC), "Weekly")
    assert monday.key != next_monday.key


ACCESS = "access:aisle-3"
START = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def meeting() -> Meeting:
    return Meeting("carla@example.com", START, f"Restock review {uuid4()}")


def recording(of: Meeting, seconds: float, id: str | None = None, version: str = "1") -> Recording:
    return Recording(id or f"rec-{uuid4()}", of, version, seconds, (ACCESS,))


def tool() -> FakeTranscriptSource:
    return FakeTranscriptSource(f"tool-{uuid4()}")


def put(source: FakeTranscriptSource, *recordings: Recording) -> None:
    for each in recordings:
        source.put(each, f"Erin: restock aisle 3 ({each.id}, {each.seconds:.0f}s).\n")


def described(store: FakeContextStore, of: Meeting) -> str | None:
    """The transcript part of what the context memory holds for the meeting."""
    kept = store.memories.get(f"meeting:{of.key}")
    return None if kept is None else kept[0].split("\n\n", 1)[1]


async def sync_meetings(
    pool: asyncpg.Pool, store: FakeContextStore, source: FakeTranscriptSource
) -> bool:
    return await sync(pool, store, MeetingConnector(pool, source, agent=AGENT), agent=AGENT)


async def state(pool: asyncpg.Pool, of: Meeting) -> str | None:
    async with pool.acquire() as connection:
        value = await connection.fetchval(
            "SELECT state FROM pxos.source_states WHERE source = $1", f"meeting:{of.key}"
        )
    return None if value is None else str(value)


async def cursor(pool: asyncpg.Pool, source: FakeTranscriptSource) -> str | None:
    async with pool.acquire() as connection:
        value = await connection.fetchval(
            "SELECT cursor FROM pxos.connector_cursors WHERE connector = $1", source.name
        )
    return None if value is None else str(value)


class TranscriptsDown(FakeTranscriptSource):
    """A tool that reports its recordings but, while down, cannot give a transcript."""

    down = False

    async def transcript(self, recording: str) -> str:
        if self.down:
            raise ConnectorUnavailable(f"{self.name} transcripts cannot be reached")
        return await super().transcript(recording)


class Vanishing(FakeTranscriptSource):
    """A tool whose recordings in ``gone`` vanished after it listed them."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.gone: set[str] = set()

    async def transcript(self, recording: str) -> str:
        if recording in self.gone:
            raise RecordingGone(recording)
        return await super().transcript(recording)


def vanishing() -> Vanishing:
    return Vanishing(f"tool-{uuid4()}")


class BrokenStore(FakeContextStore):
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        raise ConnectionResetError("context memory unavailable")

    async def forget(self, source: str) -> None:
        raise ConnectionResetError("context memory unavailable")


async def test_four_recordings_of_a_meeting_are_retained_once_from_the_longest(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    put(source, recording(weekly, 1800), recording(weekly, 3600, id="rec-b"))
    put(source, recording(weekly, 3600, id="rec-a"), recording(weekly, 900))

    assert await sync_meetings(pool, store, source) is True

    assert store.retained == [(f"meeting:{weekly.key}", MEMORY_INSTRUCTIONS)]
    assert store.memories[f"meeting:{weekly.key}"] == (
        f"Meeting: {weekly.title}\nStart: 2026-11-16T12:00:00+00:00\n"
        f"Organizer: carla@example.com\n\n{source.transcripts['rec-a']}",
        frozenset({ACCESS}),
    )
    assert await state(pool, weekly) == "available"


async def test_a_longer_recording_arriving_later_replaces_the_description(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter)
    await sync_meetings(pool, store, source)
    put(source, longer)

    await sync_meetings(pool, store, source)

    assert described(store, weekly) == source.transcripts[longer.id]
    assert len(store.retained) == 2


async def test_a_new_version_of_the_chosen_recording_replaces_the_description(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    first = recording(weekly, 1800)
    put(source, first)
    await sync_meetings(pool, store, source)
    source.put(recording(weekly, 1800, id=first.id, version="2"), "Erin: corrected.\n")

    await sync_meetings(pool, store, source)

    assert described(store, weekly) == "Erin: corrected.\n"


async def test_new_access_to_the_chosen_recording_replaces_the_descriptions_access(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    first = recording(weekly, 1800)
    put(source, first)
    await sync_meetings(pool, store, source)
    put(source, Recording(first.id, weekly, first.version, first.seconds, ("access:board",)))

    await sync_meetings(pool, store, source)

    assert store.memories[f"meeting:{weekly.key}"][1] == frozenset({"access:board"})


async def test_a_tool_unreachable_for_the_fallback_transcript_is_retried_in_the_next_pass(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = TranscriptsDown(f"tool-{uuid4()}"), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter, longer)
    await sync_meetings(pool, store, source)
    source.remove(longer.id)
    source.down = True
    assert await sync_meetings(pool, store, source) is False
    assert await cursor(pool, source) == "2"
    source.down = False

    assert await sync_meetings(pool, store, source) is True

    assert described(store, weekly) == source.transcripts[shorter.id]
    assert await cursor(pool, source) == "3"


async def test_deleting_the_chosen_recording_falls_back_to_the_next(pool: asyncpg.Pool) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter)
    await sync_meetings(pool, store, source)
    put(source, longer)
    await sync_meetings(pool, store, source)
    source.remove(longer.id)

    await sync_meetings(pool, store, source)

    assert described(store, weekly) == source.transcripts[shorter.id]
    assert await state(pool, weekly) == "available"


async def test_a_chosen_recording_gone_before_its_transcript_falls_back_in_the_same_pass(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = vanishing(), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter, longer)
    source.gone.add(longer.id)

    assert await sync_meetings(pool, store, source) is True

    assert described(store, weekly) == source.transcripts[shorter.id]
    assert len(store.retained) == 1
    assert await sync_meetings(pool, store, source) is True
    assert len(store.retained) == 1


async def test_a_meeting_whose_only_recording_is_gone_is_not_retained(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = vanishing(), FakeContextStore(), meeting()
    only = recording(weekly, 1800)
    put(source, only)
    source.gone.add(only.id)

    assert await sync_meetings(pool, store, source) is True

    assert described(store, weekly) is None
    assert await state(pool, weekly) is None
    assert await cursor(pool, source) == "1"


async def test_a_retained_meeting_whose_last_recording_is_gone_is_forgotten_in_the_same_pass(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = vanishing(), FakeContextStore(), meeting()
    only = recording(weekly, 1800)
    put(source, only)
    await sync_meetings(pool, store, source)
    source.put(recording(weekly, 1800, id=only.id, version="2"), "Erin: corrected.\n")
    source.gone.add(only.id)

    assert await sync_meetings(pool, store, source) is True

    assert described(store, weekly) is None
    assert await state(pool, weekly) == "deleted"


async def test_a_gone_recording_falling_back_to_the_one_retained_retains_nothing_new(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = vanishing(), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter)
    await sync_meetings(pool, store, source)
    put(source, longer)
    source.gone.add(longer.id)

    assert await sync_meetings(pool, store, source) is True

    assert described(store, weekly) == source.transcripts[shorter.id]
    assert len(store.retained) == 1


async def test_deleting_every_recording_forgets_the_meeting(pool: asyncpg.Pool) -> None:
    source, store, weekly, other = tool(), FakeContextStore(), meeting(), meeting()
    one, two, kept = recording(weekly, 1800), recording(weekly, 3600), recording(other, 60)
    put(source, one, two, kept)
    await sync_meetings(pool, store, source)
    source.remove(one.id)
    source.remove(two.id)

    await sync_meetings(pool, store, source)

    assert described(store, weekly) is None
    assert described(store, other) == source.transcripts[kept.id]
    assert await state(pool, weekly) == "deleted"


async def test_a_replay_retains_nothing_new(pool: asyncpg.Pool) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    put(source, recording(weekly, 1800), recording(weekly, 3600))
    await sync_meetings(pool, store, source)

    replayed = await MeetingConnector(pool, source, agent=AGENT).changes(None)

    assert (replayed.documents, replayed.deleted) == ((), ())
    await sync_meetings(pool, store, source)
    assert len(store.retained) == 1


async def test_a_fallback_that_failed_to_be_retained_is_retained_in_the_next_pass(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    shorter, longer = recording(weekly, 1800), recording(weekly, 3600)
    put(source, shorter, longer)
    await sync_meetings(pool, store, source)
    source.remove(longer.id)
    with pytest.raises(ConnectionResetError):
        await sync_meetings(pool, BrokenStore(), source)

    await sync_meetings(pool, store, source)

    assert described(store, weekly) == source.transcripts[shorter.id]


async def test_a_meeting_that_failed_to_be_forgotten_is_forgotten_in_the_next_pass(
    pool: asyncpg.Pool,
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    only = recording(weekly, 1800)
    put(source, only)
    await sync_meetings(pool, store, source)
    source.remove(only.id)
    with pytest.raises(ConnectionResetError):
        await sync_meetings(pool, BrokenStore(), source)

    await sync_meetings(pool, store, source)

    assert described(store, weekly) is None
    assert await state(pool, weekly) == "deleted"


async def test_an_unreachable_tool_is_reported_unchanged(pool: asyncpg.Pool) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    put(source, recording(weekly, 1800))
    await sync_meetings(pool, store, source)
    source.available = False

    with pytest.raises(ConnectorUnavailable):
        await MeetingConnector(pool, source, agent=AGENT).changes(None)
    assert await sync_meetings(pool, store, source) is False
    assert await state(pool, weekly) == "unavailable"


async def test_no_transcript_text_is_stored_in_postgres(
    database: Database, pool: asyncpg.Pool
) -> None:
    source, store, weekly = tool(), FakeContextStore(), meeting()
    secret = f"secret-{uuid4()}"
    only = recording(weekly, 1800)
    source.put(only, f"Erin: {secret}.\n")

    await sync_meetings(pool, store, source)

    assert secret in store.memories[f"meeting:{weekly.key}"][0]
    owner = await asyncpg.connect(database.owner_dsn)
    try:
        tables = await owner.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'pxos'")
        found = [
            row["tablename"]
            for row in tables
            if await owner.fetchval(
                f'SELECT count(*) FROM pxos."{row["tablename"]}" t WHERE t::text LIKE $1',
                f"%{secret}%",
            )
        ]
        kept = await owner.fetchval(
            "SELECT count(*) FROM pxos.meeting_recordings WHERE recording = $1", only.id
        )
    finally:
        await owner.close()
    assert found == []
    assert kept == 1


async def test_recordings_are_reached_by_agents_only(pool: asyncpg.Pool) -> None:
    source, store = tool(), FakeContextStore()
    put(source, recording(meeting(), 1800))
    await sync_meetings(pool, store, source)
    insert = """
        INSERT INTO pxos.meeting_recordings
            (source, recording, meeting, organizer, start, title, version, seconds, access)
        VALUES ($1, 'rec-x', 'k', 'o', now(), 't', '1', 1, '{}')
    """

    async with session(pool, AGENT) as connection:
        assert await connection.fetchval(
            "SELECT count(*) FROM pxos.meeting_recordings WHERE source = $1", source.name
        )
    async with session(pool, "carla") as connection:
        assert await connection.fetchval("SELECT count(*) FROM pxos.meeting_recordings") == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(insert, source.name)
    async with pool.acquire() as connection:
        with pytest.raises(asyncpg.PostgresError, match="no actor"):
            await connection.fetchval("SELECT count(*) FROM pxos.meeting_recordings")
    async with session(pool, AGENT) as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("TRUNCATE pxos.meeting_recordings")
