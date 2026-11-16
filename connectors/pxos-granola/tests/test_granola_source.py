"""Granola as a transcript source, against a fake of Granola's public API.

The fake answers with JSON shaped as https://docs.granola.ai/api-reference documents it
(OpenAPI 1.7.0): ``{notes, hasMore, cursor}`` lists, a note with ``calendar_event``, and
``{transcript, hasMore, cursor}`` transcript pages with ISO timestamps. The real workspace is
the opt-in test_granola_live.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from pxos_granola import GranolaTranscripts
from pxos_kernel.ingestion import ConnectorUnavailable
from pxos_kernel.meetings import Meeting, Recording, RecordingGone

KEY = "grn_secretkey123"
PRIVATE = "Segredo: demitir o fornecedor"


def summary(note_id: str, updated: str, deleted: str | None = None) -> dict[str, Any]:
    return {
        "id": note_id,
        "object": "note",
        "title": "Revisão do orçamento",
        "owner": {"name": "Oat Benson", "email": "oat@granola.ai"},
        "created_at": "2026-01-27T15:30:00Z",
        "updated_at": updated,
        "deleted_at": deleted,
    }


def detail(note: dict[str, Any], event: dict[str, Any] | None) -> dict[str, Any]:
    return {
        **note,
        "web_url": "https://notes.granola.ai/d/00000000-0000-0000-0000-000000000000",
        "calendar_event": event,
        "attachments": [],
        "attendees": [{"name": "Oat Benson", "email": "oat@granola.ai"}],
        "folder_membership": [],
        "space_membership": [],
        "summary_text": "Resumo da reunião",
        "summary_markdown": "## Resumo da reunião",
        "private_notes_text": PRIVATE,
        "private_notes_markdown": PRIVATE,
        "transcript": None,
    }


EVENT: dict[str, Any] = {
    "event_title": "Quarterly yoghurt budget review",
    "invitees": [{"email": "oat@granola.ai"}, {"email": "ana@granola.ai"}],
    "organiser": "ana@granola.ai",
    "calendar_event_id": "2su99n6iiik37iiknmb5t4fkfh_20260127T153000Z",
    "scheduled_start_time": "2026-01-27T15:30:00Z",
    "scheduled_end_time": "2026-01-27T16:30:00Z",
}


def item(text: str, start: str, end: str, **speaker: str) -> dict[str, Any]:
    return {
        "speaker": {"source": "microphone", **speaker},
        "text": text,
        "start_time": f"2026-01-27T{start}Z",
        "end_time": f"2026-01-27T{end}Z",
    }


TRANSCRIPT = [
    item("Bom dia a todos.", "15:31:00", "15:31:04", attribution="me", name="Oat Benson"),
    item("Vamos ao orçamento.", "15:31:05", "15:31:09", diarization_label="Speaker A"),
    item("Concordo.", "15:32:10", "15:32:12", attribution="them"),
    item("Fechado.", "15:33:00", "15:33:30", source="speaker"),
]


class FakeGranola:
    """Granola's public API in memory, paging lists ``page`` items at a time."""

    def __init__(self, page: int = 2) -> None:
        self.page = page
        self.notes: list[dict[str, Any]] = []
        self.details: dict[str, dict[str, Any]] = {}
        self.transcripts: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[httpx.Request] = []
        self.statuses: list[int] = []

    def add(self, note: dict[str, Any], event: dict[str, Any] | None = EVENT) -> None:
        self.notes.append(note)
        self.details[note["id"]] = detail(note, event)
        self.transcripts[note["id"]] = TRANSCRIPT

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.statuses:
            return httpx.Response(self.statuses.pop(0), json={"code": "RATE_LIMITED"})
        parts = request.url.path.removeprefix("/v1/notes").strip("/").split("/")
        if parts == [""]:
            return self._page(request, "notes", self._listed(request.url.params))
        if parts[0] not in self.details:
            return httpx.Response(404, json={"code": "NOTE_NOT_FOUND", "message": "Not found"})
        if len(parts) == 1:
            return httpx.Response(200, json=self.details[parts[0]])
        return self._page(request, "transcript", self.transcripts[parts[0]])

    def _listed(self, params: httpx.QueryParams) -> list[dict[str, Any]]:
        after = params.get("updated_after")
        return [n for n in self.notes if not after or parse(n["updated_at"]) > parse(after)]

    def _page(self, request: httpx.Request, key: str, items: list[Any]) -> httpx.Response:
        start = int(request.url.params.get("cursor", "0"))
        end = start + self.page
        more = end < len(items)
        body = {key: items[start:end], "hasMore": more, "cursor": str(end) if more else None}
        return httpx.Response(200, json=body)

    def source(self, **config: Any) -> GranolaTranscripts:
        self.slept: list[float] = []

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        return GranolaTranscripts(
            KEY,
            access=("corporate",),
            transport=httpx.MockTransport(self),
            sleep=sleep,
            **config,
        )


def parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=UTC)


async def test_a_note_with_a_calendar_event_is_a_recording_of_that_meeting() -> None:
    granola = FakeGranola()
    granola.add(summary("not_1d3tmYTlCICgjy", "2026-01-27T16:45:00Z"))

    changes = await granola.source().changes(None)

    assert changes.recordings == (
        Recording(
            id="not_1d3tmYTlCICgjy",
            meeting=Meeting("ana@granola.ai", at("2026-01-27T15:30:00"), EVENT["event_title"]),
            version="2026-01-27T16:45:00Z",
            seconds=150.0,
            access=("corporate",),
        ),
    )
    assert changes.removed == ()
    assert changes.cursor == "2026-01-27T16:45:00Z"
    assert all(r.headers["Authorization"] == f"Bearer {KEY}" for r in granola.requests)
    assert granola.requests[0].url.params["page_size"] == "30"
    assert "updated_after" not in granola.requests[0].url.params


async def test_a_note_without_calendar_event_is_its_owners_note_when_created() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"), event=None)
    orphan_event = {**EVENT, "organiser": None, "scheduled_start_time": None}
    granola.add(summary("not_bbbbbbbbbbbbbb", "2026-01-27T16:45:00Z"), event=orphan_event)

    changes = await granola.source().changes(None)

    owner = Meeting("oat@granola.ai", at("2026-01-27T15:30:00"), "Revisão do orçamento")
    assert [r.meeting for r in changes.recordings] == [owner, owner]


async def test_an_event_without_title_names_every_attendees_note_alike() -> None:
    granola = FakeGranola()
    granola.add(
        summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"), {**EVENT, "event_title": None}
    )

    (recording,) = (await granola.source().changes(None)).recordings

    assert recording.meeting == Meeting("ana@granola.ai", at("2026-01-27T15:30:00"), "")


async def test_listing_and_transcripts_are_read_through_every_page() -> None:
    granola = FakeGranola(page=2)
    for n in range(5):
        granola.add(summary(f"not_{n:014d}", f"2026-01-27T16:4{n}:00Z"))

    changes = await granola.source().changes(None)

    assert [r.id for r in changes.recordings] == [f"not_{n:014d}" for n in range(5)]
    assert {r.seconds for r in changes.recordings} == {150.0}
    assert changes.cursor == "2026-01-27T16:44:00Z"


async def test_the_next_poll_overlaps_the_cursor_so_a_late_note_at_the_same_time_counts() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00.250Z"))
    source = granola.source()
    first = await source.changes(None)
    granola.add(summary("not_bbbbbbbbbbbbbb", "2026-01-27T16:45:00.250Z"))
    granola.requests.clear()

    second = await source.changes(first.cursor)

    assert granola.requests[0].url.params["updated_after"] == "2026-01-27T16:44:59.250000+00:00"
    assert [r.id for r in second.recordings] == ["not_aaaaaaaaaaaaaa", "not_bbbbbbbbbbbbbb"]
    assert second.cursor == first.cursor


async def test_a_poll_without_changes_keeps_the_cursor() -> None:
    source = FakeGranola().source()

    assert (await source.changes(None)).cursor == ""
    assert (await source.changes("2026-01-27T16:45:00Z")).cursor == "2026-01-27T16:45:00Z"


async def test_deleted_notes_and_notes_gone_before_reading_are_removed() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T17:00:00Z", "2026-01-27T17:00:00Z"))
    granola.add(summary("not_bbbbbbbbbbbbbb", "2026-01-27T16:00:00Z"))
    del granola.details["not_bbbbbbbbbbbbbb"]

    changes = await granola.source().changes(None)

    assert changes.recordings == ()
    assert changes.removed == ("not_aaaaaaaaaaaaaa", "not_bbbbbbbbbbbbbb")
    assert changes.cursor == "2026-01-27T17:00:00Z"
    assert len(granola.requests) == 2


async def test_listing_keeps_to_the_configured_space_and_folder() -> None:
    granola = FakeGranola()

    await granola.source(space_id="spa_1d3tmYTlCICgjy", folder_id="fol_1d3tmYTlCICgjy").changes(
        None
    )

    params = granola.requests[0].url.params
    assert (params["space_id"], params["folder_id"]) == ("spa_1d3tmYTlCICgjy", "fol_1d3tmYTlCICgjy")


async def test_a_transcript_renders_each_line_with_its_time_and_speaker() -> None:
    granola = FakeGranola(page=3)
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"))

    text = await granola.source().transcript("not_aaaaaaaaaaaaaa")

    assert text == (
        "[0:00:00] Oat Benson: Bom dia a todos.\n"
        "[0:00:05] Speaker A: Vamos ao orçamento.\n"
        "[0:01:10] them: Concordo.\n"
        "[0:02:00] speaker: Fechado."
    )


async def test_an_empty_transcript_covers_nothing() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"))
    granola.transcripts["not_aaaaaaaaaaaaaa"] = []
    source = granola.source()

    assert (await source.changes(None)).recordings[0].seconds == 0.0
    assert await source.transcript("not_aaaaaaaaaaaaaa") == ""


async def test_private_notes_never_leave_the_source() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"))
    source = granola.source()

    changes = await source.changes(None)
    text = await source.transcript("not_aaaaaaaaaaaaaa")

    assert PRIVATE not in repr(changes) + text
    assert all("include" not in r.url.params for r in granola.requests)


async def test_the_transcript_of_a_note_no_longer_visible_is_gone() -> None:
    with pytest.raises(RecordingGone, match="not_aaaaaaaaaaaaaa"):
        await FakeGranola().source().transcript("not_aaaaaaaaaaaaaa")


async def test_a_rate_limited_request_backs_off_and_retries() -> None:
    granola = FakeGranola()
    granola.add(summary("not_aaaaaaaaaaaaaa", "2026-01-27T16:45:00Z"))
    granola.statuses = [429, 429, 429]
    source = granola.source()

    changes = await source.changes(None)

    assert granola.slept == [1.0, 2.0, 4.0]
    assert [r.id for r in changes.recordings] == ["not_aaaaaaaaaaaaaa"]


async def test_a_rate_limit_that_never_lifts_makes_granola_unavailable() -> None:
    granola = FakeGranola()
    granola.statuses = [429] * 10

    with pytest.raises(ConnectorUnavailable):
        await granola.source().changes(None)
    assert granola.slept == [1.0, 2.0, 4.0, 8.0, 16.0]


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)


@pytest.mark.parametrize(
    "transport",
    [
        httpx.MockTransport(refuse),
        httpx.MockTransport(lambda _: httpx.Response(401, json={"code": "INVALID_API_KEY"})),
        httpx.MockTransport(lambda _: httpx.Response(500, text=PRIVATE)),
    ],
    ids=["unreachable", "refused key", "server error"],
)
async def test_granola_unreachable_is_unavailable_without_leaking(
    transport: httpx.MockTransport,
) -> None:
    source = GranolaTranscripts(KEY, access=("corporate",), transport=transport)

    for call in (source.changes(None), source.transcript("not_aaaaaaaaaaaaaa")):
        with pytest.raises(ConnectorUnavailable) as raised:
            await call
        assert KEY not in str(raised.value) and PRIVATE not in str(raised.value)
        error = raised.value
        assert error.__cause__ is None and (error.__suppress_context__ or not error.__context__)


async def test_a_key_whose_scope_cannot_list_notes_makes_granola_unavailable() -> None:
    not_found = httpx.MockTransport(lambda _: httpx.Response(404, json={"code": "NOT_FOUND"}))
    source = GranolaTranscripts(KEY, access=("corporate",), transport=not_found)

    with pytest.raises(ConnectorUnavailable, match="404"):
        await source.changes(None)
