"""The SharePoint connector speaks Microsoft Graph (drive delta, item content) and the
identity platform's client credentials grant.

A fake server answers with bodies shaped as the official docs show them: the delta pages
of driveitem-delta, with the driveItem properties of the driveItem resource, the 410 of a
resync, the 302 of driveitem-get-content and the throttling responses. The real tenant is
the opt-in test (test_sharepoint_live).
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from guilda_kernel.ingestion import ConnectorUnavailable, SourceDocument
from guilda_sharepoint import ClientSecret, SharePointConnector

GRAPH = "https://graph.microsoft.com/v1.0"
TOKEN = "https://login.microsoftonline.com/tenant-a/oauth2/v2.0/token"
DRIVE = "b!drive-a"
START = f"{GRAPH}/drives/{DRIVE}/root/delta"
NEXT = f"{GRAPH}/drives/{DRIVE}/root/delta?token=page-2"
DELTA = f"{GRAPH}/drives/{DRIVE}/root/delta?token=delta-1"

Answer = httpx.Response | Callable[[httpx.Request], httpx.Response]


def item(id: str, name: str, **extra: Any) -> dict[str, Any]:
    """A file as delta returns it in a document library."""
    return {
        "id": id,
        "name": name,
        "eTag": f'"{{{id}}},3"',
        "lastModifiedDateTime": "2026-11-10T14:00:00Z",
        "size": 120,
        "webUrl": f"https://contoso.sharepoint.com/sites/rh/Documentos/{name}",
        "parentReference": {"driveId": DRIVE, "driveType": "documentLibrary", "id": "root-id"},
        "file": {"mimeType": "text/plain"},
        **extra,
    }


FOLDER = {"id": "root-id", "name": "root", "folder": {"childCount": 2}, "root": {}}


def page(*items: dict[str, Any], next: str | None = None, delta: str | None = None) -> Any:
    body: dict[str, Any] = {"value": list(items)}
    if next:
        body["@odata.nextLink"] = next
    if delta:
        body["@odata.deltaLink"] = delta
    return httpx.Response(200, json=body)


class FakeGraph:
    """Answers each URL with its queued responses, in order, and records every request."""

    def __init__(self, routes: dict[str, list[Answer]]) -> None:
        self.routes: dict[str, list[Answer]] = {TOKEN: [self.token()], **routes}
        self.requests: list[httpx.Request] = []

    @staticmethod
    def token(access: str = "token-1") -> httpx.Response:
        body = {"token_type": "Bearer", "expires_in": 3599, "access_token": access}
        return httpx.Response(200, json=body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.routes[str(request.url)]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return answer(request) if callable(answer) else answer

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests if str(request.url) != TOKEN]


def connector(
    graph: FakeGraph, waits: list[float] | None = None, **settings: Any
) -> SharePointConnector:
    """A connector to the fake server; the waits it would sleep are recorded in ``waits``."""
    recorded = [] if waits is None else waits

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)

    configured: dict[str, Any] = {
        "tenant": "tenant-a",
        "client_id": "app-a",
        "credential": ClientSecret("s3cr3t"),
        "drive_id": DRIVE,
        "access": ("corporate",),
        **settings,
    }
    return SharePointConnector(
        "sharepoint-rh", transport=httpx.MockTransport(graph), sleep=sleep, **configured
    )


def content(id: str, text: str) -> dict[str, list[Answer]]:
    return {f"{GRAPH}/drives/{DRIVE}/items/{id}/content": [httpx.Response(200, text=text)]}


async def test_the_first_pass_pages_the_delta_from_the_start() -> None:
    graph = FakeGraph(
        {
            START: [page(FOLDER, item("01A", "ferias.txt"), next=NEXT)],
            NEXT: [page(item("01B", "beneficios.md"), delta=DELTA)],
            **content("01A", "Férias em 30 dias."),
            **content("01B", "Plano de saúde."),
        }
    )

    changes = await connector(graph).changes(None)

    assert changes.cursor == DELTA
    assert changes.documents == (
        SourceDocument(
            source=f"sharepoint-rh:{DRIVE}:01A",
            version='"{01A},3"',
            text="ferias.txt\nhttps://contoso.sharepoint.com/sites/rh/Documentos/ferias.txt"
            "\n\nFérias em 30 dias.",
            access=("corporate",),
        ),
        SourceDocument(
            source=f"sharepoint-rh:{DRIVE}:01B",
            version='"{01B},3"',
            text="beneficios.md\nhttps://contoso.sharepoint.com/sites/rh/Documentos/beneficios.md"
            "\n\nPlano de saúde.",
            access=("corporate",),
        ),
    )
    assert (changes.deleted, changes.revoked) == ((), ())


async def test_the_app_authenticates_once_with_client_credentials() -> None:
    graph = FakeGraph({START: [page(item("01A", "a.txt"), delta=DELTA)], **content("01A", "x")})

    await connector(graph).changes(None)

    token, *calls = graph.requests
    assert (token.method, str(token.url)) == ("POST", TOKEN)
    assert parse_qs(token.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["app-a"],
        "scope": ["https://graph.microsoft.com/.default"],
        "client_secret": ["s3cr3t"],
    }
    assert {call.headers["Authorization"] for call in calls} == {"Bearer token-1"}


async def test_an_expired_token_is_renewed(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeGraph({DELTA: [page(delta=DELTA)]})
    graph.routes[TOKEN] = [FakeGraph.token("token-1"), FakeGraph.token("token-2")]
    clock = [1000.0]
    monkeypatch.setattr("guilda_sharepoint.connector.time.monotonic", lambda: clock[0])
    sharepoint = connector(graph)

    await sharepoint.changes(DELTA)
    clock[0] += 3599 - 60
    await sharepoint.changes(DELTA)

    assert [r.headers["Authorization"] for r in graph.requests if str(r.url) == DELTA] == [
        "Bearer token-1",
        "Bearer token-2",
    ]


async def test_a_later_pass_resumes_from_the_cursor_and_reports_deletions() -> None:
    deleted = {"id": "01C", "deleted": {"state": "deleted"}, "parentReference": {"driveId": DRIVE}}
    graph = FakeGraph({DELTA: [page(deleted, delta=f"{DELTA}2")]})

    changes = await connector(graph).changes(DELTA)

    assert graph.urls() == [DELTA]
    assert changes.deleted == (f"sharepoint-rh:{DRIVE}:01C",)
    assert (changes.documents, changes.cursor) == ((), f"{DELTA}2")


async def test_the_last_occurrence_of_an_item_wins() -> None:
    graph = FakeGraph(
        {
            START: [page(item("01A", "a.txt"), {"id": "01B", "deleted": {}}, next=NEXT)],
            NEXT: [page({"id": "01A", "deleted": {}}, item("01B", "b.txt"), delta=DELTA)],
            **content("01B", "volta"),
        }
    )

    changes = await connector(graph).changes(None)

    assert changes.deleted == (f"sharepoint-rh:{DRIVE}:01A",)
    assert [d.source for d in changes.documents] == [f"sharepoint-rh:{DRIVE}:01B"]
    assert f"{GRAPH}/drives/{DRIVE}/items/01A/content" not in graph.urls()


async def test_a_folder_scopes_the_delta_to_it() -> None:
    folder_delta = f"{GRAPH}/drives/{DRIVE}/items/folder-1/delta"
    graph = FakeGraph({folder_delta: [page(delta=DELTA)]})

    await connector(graph, folder_id="folder-1").changes(None)

    assert graph.urls() == [folder_delta]


async def test_a_resync_restarts_from_the_location_the_service_gives() -> None:
    fresh = f"{GRAPH}/drives/{DRIVE}/root/delta?token=fresh"
    gone = {"error": {"code": "resyncRequired", "message": "Resync required."}}
    graph = FakeGraph(
        {
            DELTA: [httpx.Response(410, json=gone, headers={"Location": fresh})],
            fresh: [page(item("01A", "a.txt"), delta=f"{DELTA}2")],
            **content("01A", "tudo de novo"),
        }
    )

    changes = await connector(graph).changes(DELTA)

    assert [d.source for d in changes.documents] == [f"sharepoint-rh:{DRIVE}:01A"]
    assert changes.cursor == f"{DELTA}2"


async def test_a_resync_without_location_restarts_from_the_start() -> None:
    gone = {"error": {"code": "resyncChangesApplyDifferences", "message": "Resync."}}
    graph = FakeGraph({DELTA: [httpx.Response(410, json=gone)], START: [page(delta=f"{DELTA}2")]})

    changes = await connector(graph).changes(DELTA)

    assert graph.urls() == [DELTA, START]
    assert changes.cursor == f"{DELTA}2"


async def test_a_resync_that_repeats_makes_the_library_unavailable() -> None:
    gone = httpx.Response(410, json={"error": {"code": "resyncRequired", "message": "x"}})
    graph = FakeGraph({DELTA: [gone], START: [gone]})

    with pytest.raises(ConnectorUnavailable, match="410 resyncRequired"):
        await connector(graph).changes(DELTA)


async def test_throttling_waits_as_long_as_retry_after_says() -> None:
    throttled = {"error": {"code": "activityLimitReached", "message": "Throttled."}}
    graph = FakeGraph(
        {
            DELTA: [
                httpx.Response(429, json=throttled, headers={"Retry-After": "10"}),
                httpx.Response(503, json=throttled),
                page(delta=f"{DELTA}2"),
            ]
        }
    )
    waits: list[float] = []

    changes = await connector(graph, waits).changes(DELTA)

    assert changes.cursor == f"{DELTA}2"
    assert waits == [10.0, 2.0]


async def test_throttling_past_the_retries_makes_the_library_unavailable() -> None:
    graph = FakeGraph({DELTA: [httpx.Response(429, headers={"Retry-After": "1"})]})
    waits: list[float] = []

    with pytest.raises(ConnectorUnavailable, match="429"):
        await connector(graph, waits, retries=2).changes(DELTA)

    assert waits == [1.0, 1.0]


async def test_an_unreachable_graph_makes_the_library_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    graph = FakeGraph({DELTA: [refuse]})

    with pytest.raises(ConnectorUnavailable, match="sharepoint-rh: ConnectError") as raised:
        await connector(graph).changes(DELTA)

    assert raised.value.__cause__ is None


@pytest.mark.parametrize(("status", "code"), [(403, "accessDenied"), (404, "itemNotFound")])
async def test_losing_the_drive_makes_the_library_unavailable(status: int, code: str) -> None:
    body = {"error": {"code": code, "message": "Pasta de salários confidencial"}}
    graph = FakeGraph({DELTA: [httpx.Response(status, json=body)]})

    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(graph).changes(DELTA)

    assert str(raised.value) == f"sharepoint-rh: Graph answered {status} {code}"


async def test_a_refused_token_names_the_error_without_the_secret() -> None:
    refused = {"error": "invalid_client", "error_description": "AADSTS7000215: s3cr3t"}
    graph = FakeGraph({DELTA: [page(delta=DELTA)]})
    graph.routes[TOKEN] = [httpx.Response(401, json=refused)]

    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(graph).changes(DELTA)

    assert str(raised.value) == "sharepoint-rh: Graph answered 401 invalid_client"


async def test_an_error_without_a_json_body_names_only_the_status() -> None:
    graph = FakeGraph({DELTA: [httpx.Response(502, text="<html>Bad gateway</html>")]})

    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(graph).changes(DELTA)

    assert str(raised.value) == "sharepoint-rh: Graph answered 502"


async def test_content_follows_the_redirect_without_the_token() -> None:
    download = "https://contoso.sharepoint.com/_layouts/15/download.aspx?tempauth=abc"
    graph = FakeGraph(
        {
            DELTA: [page(item("01A", "a.txt"), delta=f"{DELTA}2")],
            f"{GRAPH}/drives/{DRIVE}/items/01A/content": [
                httpx.Response(302, headers={"Location": download})
            ],
            download: [httpx.Response(200, text="conteúdo")],
        }
    )

    changes = await connector(graph).changes(DELTA)

    assert changes.documents[0].text.endswith("\n\nconteúdo")
    fetched = next(r for r in graph.requests if str(r.url) == download)
    assert "Authorization" not in fetched.headers


async def test_files_that_cannot_be_read_are_skipped() -> None:
    graph = FakeGraph(
        {
            DELTA: [
                page(
                    item("01A", "foto.png"),
                    item("01B", "enorme.txt", size=2_000),
                    item("01C", "corrompido.docx"),
                    item("01D", "vazio.txt"),
                    item("01F", "certo.txt"),
                    delta=f"{DELTA}2",
                )
            ],
            **content("01C", "não é zip"),
            **content("01D", "  \n"),
            **content("01F", "ok"),
        }
    )

    changes = await connector(graph, max_bytes=1_000).changes(DELTA)

    assert [d.source for d in changes.documents] == [f"sharepoint-rh:{DRIVE}:01F"]
    assert changes.deleted == ()
    fetched = graph.urls()
    assert not any("/01A/" in url or "/01B/" in url for url in fetched)


@pytest.mark.parametrize("status", [403, 404, 423])
async def test_a_file_the_service_refuses_is_skipped(status: int) -> None:
    graph = FakeGraph(
        {
            DELTA: [page(item("01A", "a.txt"), item("01B", "b.txt"), delta=f"{DELTA}2")],
            f"{GRAPH}/drives/{DRIVE}/items/01A/content": [httpx.Response(status)],
            **content("01B", "ok"),
        }
    )

    changes = await connector(graph).changes(DELTA)

    assert [d.source for d in changes.documents] == [f"sharepoint-rh:{DRIVE}:01B"]


@pytest.mark.parametrize("status", [401, 500])
async def test_a_failed_download_makes_the_library_unavailable(status: int) -> None:
    graph = FakeGraph(
        {
            DELTA: [page(item("01A", "a.txt"), delta=f"{DELTA}2")],
            f"{GRAPH}/drives/{DRIVE}/items/01A/content": [httpx.Response(status)],
        }
    )

    with pytest.raises(ConnectorUnavailable, match=str(status)):
        await connector(graph).changes(DELTA)


async def test_without_an_etag_the_version_is_the_last_modification() -> None:
    untagged = item("01A", "a.txt")
    del untagged["eTag"]
    graph = FakeGraph({DELTA: [page(untagged, delta=f"{DELTA}2")], **content("01A", "x")})

    changes = await connector(graph).changes(DELTA)

    assert changes.documents[0].version == "2026-11-10T14:00:00Z"


def test_a_library_needs_access_tags() -> None:
    with pytest.raises(ValueError, match="access tag"):
        connector(FakeGraph({}), access=())
