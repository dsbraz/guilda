"""Granola's public API as a transcript source, read with a workspace API key.

Each Granola note is a recording. Its meeting is the note's calendar event (organiser,
scheduled start, event title), the same for every attendee's note; a note without a usable
event (no event, or no organiser or start) is a meeting of its own, named by the note's
owner, creation time and title. The version is the note's ``updated_at``, and ``seconds`` is
the time the transcript spans, read from the transcript itself because Granola exposes no
duration. The access tags are configured: a workspace key reads only notes shared in the
workspace's spaces, optionally narrowed to one space or folder.

The cursor is the latest ``updated_at`` listed. Granola documents ``updated_after`` only as
"updated after", so each poll asks from one second before the cursor: notes updated at the
boundary come again whether the filter is strict or not, and the kernel's idempotence
absorbs them. Granola lists only notes with a generated summary.

Removed recordings are listed notes with ``deleted_at`` (Granola shows them only to keys
that reach every workspace note) and notes that vanish between listing and reading. A note
that leaves the key's reach without changing (unshared, moved out of the space, space
closed to the API) is not in any delta; Granola's webhooks do not announce it either.

The private notes of a note are never read. A rate-limited request waits and retries; when
Granola cannot be reached, refuses the key or its scope cannot list notes (404),
``ConnectorUnavailable`` names only the
error's type or status, never content.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

from pxos_kernel.ingestion import ConnectorUnavailable
from pxos_kernel.meetings import Meeting, Recording, RecordingChanges

BASE_URL = "https://public-api.granola.ai"
BACKOFF = (1.0, 2.0, 4.0, 8.0, 16.0)
OVERLAP = timedelta(seconds=1)


class RecordingNotFound(LookupError):
    """The recording does not exist or the key no longer reaches it."""


class GranolaTranscripts:
    name = "granola"

    def __init__(
        self,
        api_key: str,
        access: tuple[str, ...],
        *,
        space_id: str | None = None,
        folder_id: str | None = None,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
            transport=transport,
        )
        self._access = access
        scope = {"space_id": space_id, "folder_id": folder_id}
        self._scope = {key: value for key, value in scope.items() if value}
        self._sleep = sleep

    async def changes(self, cursor: str | None) -> RecordingChanges:
        params = {"page_size": "30", **self._scope}
        if cursor:
            params["updated_after"] = (_time(cursor) - OVERLAP).isoformat()
        notes = [note async for note in self._pages("/v1/notes", params, "notes")]
        recordings: list[Recording] = []
        removed: list[str] = []
        for note in notes:
            try:
                if note["deleted_at"]:
                    raise RecordingNotFound(note["id"])
                recordings.append(await self._recording(note))
            except RecordingNotFound:
                removed.append(note["id"])
        stamps = [note["updated_at"] for note in notes] + ([cursor] if cursor else [])
        return RecordingChanges(
            cursor=max(stamps, key=_time, default=""),
            recordings=tuple(recordings),
            removed=tuple(removed),
        )

    async def transcript(self, recording: str) -> str:
        items = await self._transcript(recording)
        if not items:
            return ""
        origin = min(_time(item["start_time"]) for item in items)
        return "\n".join(_line(item, origin) for item in items)

    async def _recording(self, note: dict[str, Any]) -> Recording:
        detail = await self._get(f"/v1/notes/{note['id']}", {})
        items = await self._transcript(note["id"])
        return Recording(
            id=note["id"],
            meeting=_meeting(detail),
            version=note["updated_at"],
            seconds=_coverage(items),
            access=self._access,
        )

    async def _transcript(self, recording: str) -> list[dict[str, Any]]:
        path = f"/v1/notes/{recording}/transcript"
        return [item async for item in self._pages(path, {"page_size": "100"}, "transcript")]

    async def _pages(
        self, path: str, params: dict[str, str], key: str
    ) -> AsyncIterator[dict[str, Any]]:
        page = await self._get(path, params)
        for item in page[key]:
            yield item
        while page["hasMore"]:
            page = await self._get(path, {**params, "cursor": page["cursor"]})
            for item in page[key]:
                yield item

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        response = await self._send(path, params)
        for delay in BACKOFF:
            if response.status_code != 429:
                break
            await self._sleep(delay)
            response = await self._send(path, params)
        if response.status_code == 404 and path != "/v1/notes":
            raise RecordingNotFound(path.split("/")[3])
        if response.is_error:
            raise ConnectorUnavailable(f"granola answered {response.status_code}")
        body: dict[str, Any] = response.json()
        return body

    async def _send(self, path: str, params: dict[str, str]) -> httpx.Response:
        try:
            return await self._client.get(path, params=params)
        except httpx.TransportError as error:
            raise ConnectorUnavailable(
                f"granola cannot be reached: {type(error).__name__}"
            ) from None


def _meeting(note: dict[str, Any]) -> Meeting:
    event = note["calendar_event"] or {}
    if event.get("organiser") and event.get("scheduled_start_time"):
        start = _time(event["scheduled_start_time"])
        return Meeting(event["organiser"], start, event["event_title"] or "")
    return Meeting(note["owner"]["email"], _time(note["created_at"]), note["title"] or "")


def _coverage(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    start = min(_time(item["start_time"]) for item in items)
    return (max(_time(item["end_time"]) for item in items) - start).total_seconds()


def _line(item: dict[str, Any], origin: datetime) -> str:
    speaker = item["speaker"]
    who = (
        speaker.get("name")
        or speaker.get("diarization_label")
        or speaker.get("attribution")
        or speaker["source"]
    )
    return f"[{_time(item['start_time']) - origin}] {who}: {item['text']}"


def _time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)
