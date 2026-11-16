"""A SharePoint document library as the kernel's document base (ADR 0006 §1).

The app authenticates with client credentials, granted ``Sites.Selected`` on the library's
site, and reads the drive's delta: the first pass starts without a token, each
``@odata.nextLink`` is a page and the ``@odata.deltaLink`` is the cursor. An item may
appear more than once; its last occurrence wins. Items with the ``deleted`` facet are
deleted sources. A 410 restarts the enumeration once, from the ``Location`` the service
gives or from the start. Each new or changed file is downloaded and its text delivered with
its name and web URL as provenance, under the library's access tags: access follows the
library, not each document's permissions.

A file the connector cannot read (unsupported, too large, corrupt, empty, or refused by
the service with a 4xx other than 401) is skipped, so it never holds back the cursor.
Throttling (429, 503) waits as ``Retry-After`` says, or backs off exponentially, a bounded
number of times. Any other failure, including losing access to the drive, raises
ConnectorUnavailable with the status and the Graph error code only: never a URL, which may
be pre-authenticated, nor content.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from pxos_kernel.ingestion import Changes, ConnectorUnavailable, SourceDocument
from pxos_sharepoint.credentials import ClientCertificate, ClientSecret
from pxos_sharepoint.extraction import UnreadableDocument, extract_text, supported

GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"
_THROTTLED = {httpx.codes.TOO_MANY_REQUESTS, httpx.codes.SERVICE_UNAVAILABLE}
_RENEW_BEFORE = 60


class SharePointConnector:
    def __init__(
        self,
        name: str,
        *,
        tenant: str,
        client_id: str,
        credential: ClientSecret | ClientCertificate,
        drive_id: str,
        access: Sequence[str],
        folder_id: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
        retries: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """``name`` keys the connector's cursor and prefixes its sources, so each library
        needs its own; ``folder_id`` scopes it to one folder's subtree."""
        if not access:
            raise ValueError("a library needs at least one access tag")
        self.name = name
        self._token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        self._client_id = client_id
        self._credential = credential
        self._drive = f"{GRAPH}/drives/{drive_id}"
        self._drive_id = drive_id
        root = f"items/{folder_id}" if folder_id else "root"
        self._start = f"{self._drive}/{root}/delta"
        self._access = tuple(access)
        self._max_bytes = max_bytes
        self._retries = retries
        self._sleep = sleep
        self._client = httpx.AsyncClient(timeout=60, transport=transport, follow_redirects=True)
        self._token: tuple[str, float] | None = None

    async def changes(self, cursor: str | None) -> Changes:
        """The delta since ``cursor``, a delta link, or from the start when None."""
        cursor, items = await self._delta(cursor or self._start)
        documents, deleted = [], []
        for item in items.values():
            source = f"{self.name}:{self._drive_id}:{item['id']}"
            if "deleted" in item:
                deleted.append(source)
            elif "file" in item and (document := await self._document(source, item)):
                documents.append(document)
        return Changes(cursor=cursor, documents=tuple(documents), deleted=tuple(deleted))

    async def _delta(self, url: str) -> tuple[str, dict[str, dict[str, Any]]]:
        items: dict[str, dict[str, Any]] = {}
        restarted = False
        while True:
            response = await self._graph(url)
            if response.status_code == httpx.codes.GONE and not restarted:
                url, restarted = response.headers.get("Location", self._start), True
                continue
            page = self._ok(response).json()
            items.update((item["id"], item) for item in page["value"])
            if "@odata.deltaLink" in page:
                return page["@odata.deltaLink"], items
            url = page["@odata.nextLink"]

    async def _document(self, source: str, item: dict[str, Any]) -> SourceDocument | None:
        name = item["name"]
        if item.get("size", 0) > self._max_bytes or not supported(name):
            return None
        response = await self._graph(f"{self._drive}/items/{item['id']}/content")
        if response.is_client_error and response.status_code != httpx.codes.UNAUTHORIZED:
            return None
        try:
            body = extract_text(name, self._ok(response).content)
        except UnreadableDocument:
            return None
        if not body:
            return None
        return SourceDocument(
            source=source,
            version=item.get("eTag") or item["lastModifiedDateTime"],
            text=f"{name}\n{item['webUrl']}\n\n{body}",
            access=self._access,
        )

    async def _graph(self, url: str) -> httpx.Response:
        token = await self._access_token()
        return await self._send("GET", url, headers={"Authorization": f"Bearer {token}"})

    async def _access_token(self) -> str:
        if self._token is None or self._token[1] <= time.monotonic():
            form = {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "scope": _SCOPE,
                **self._credential.form(self._client_id, self._token_url),
            }
            body = self._ok(await self._send("POST", self._token_url, data=form)).json()
            expires = time.monotonic() + body["expires_in"] - _RENEW_BEFORE
            self._token = (body["access_token"], expires)
        return self._token[0]

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                raise ConnectorUnavailable(f"{self.name}: {type(error).__name__}") from None
            if response.status_code not in _THROTTLED or attempt == self._retries:
                return response
            await self._sleep(_retry_after(response, attempt))
            attempt += 1

    def _ok(self, response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response
        code = _error_code(response)
        raise ConnectorUnavailable(
            f"{self.name}: Graph answered {response.status_code}{f' {code}' if code else ''}"
        )


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return float(2**attempt)


def _error_code(response: httpx.Response) -> str | None:
    """Graph's ``error.code``, or the identity platform's ``error``; never the message."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    if isinstance(error, dict):
        error = error.get("code")
    return error if isinstance(error, str) else None
