"""The connector can read as a signed-in user, for development: a delegated credential.

A public client (no secret) trades the user's refresh token, kept in a file by the login
command, for an access token, as the identity platform's refresh token grant specifies; the
service rotates the refresh token and the connector keeps the new one in the same file.
"""

import stat
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from pxos_kernel.ingestion import ConnectorUnavailable
from pxos_sharepoint import DelegatedToken, SharePointConnector

TOKEN = "https://login.microsoftonline.com/tenant-a/oauth2/v2.0/token"
DELTA = "https://graph.microsoft.com/v1.0/drives/b!drive-a/root/delta?token=delta-1"
SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"
RELOGIN = "run python -m pxos_sharepoint.login again"


def granted(access: str, refresh: str | None) -> httpx.Response:
    """A refresh token grant's answer, as the identity platform documents it."""
    body = {"token_type": "Bearer", "scope": SCOPE, "expires_in": 3599, "access_token": access}
    if refresh:
        body["refresh_token"] = refresh
    return httpx.Response(200, json=body)


class Entra:
    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN:
            return self.answers.pop(0)
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": DELTA})

    def token_forms(self) -> list[dict[str, list[str]]]:
        return [parse_qs(r.content.decode()) for r in self.requests if str(r.url) == TOKEN]


def connector(entra: Entra, path: Path) -> SharePointConnector:
    return SharePointConnector(
        "sharepoint-rh",
        tenant="tenant-a",
        client_id="app-a",
        credential=DelegatedToken(path),
        drive_id="b!drive-a",
        access=("corporate",),
        transport=httpx.MockTransport(entra),
    )


def stored(path: Path, token: str) -> Path:
    path.write_text(token)
    path.chmod(0o644)
    return path


def test_the_credential_reads_the_refresh_token_from_its_file_at_each_request(
    tmp_path: Path,
) -> None:
    path = stored(tmp_path / "token", "refresh-1\n")
    credential = DelegatedToken(path)

    first = credential.form("app-a", TOKEN)
    path.write_text("refresh-2")

    assert first == {"grant_type": "refresh_token", "refresh_token": "refresh-1", "scope": SCOPE}
    assert credential.form("app-a", TOKEN)["refresh_token"] == "refresh-2"


def test_a_rotated_refresh_token_replaces_the_old_one_readable_only_by_its_owner(
    tmp_path: Path,
) -> None:
    path = stored(tmp_path / "token", "refresh-1")
    credential = DelegatedToken(path)

    credential.keep({"access_token": "access-1"})
    assert path.read_text() == "refresh-1"

    credential.keep({"access_token": "access-1", "refresh_token": "refresh-2"})
    assert path.read_text() == "refresh-2"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


async def test_the_user_reads_through_a_public_client_and_the_token_rotates(
    tmp_path: Path,
) -> None:
    path = stored(tmp_path / "token", "refresh-1")
    entra = Entra(granted("access-1", "refresh-2"))
    sharepoint = connector(entra, path)

    await sharepoint.changes(DELTA)
    await sharepoint.changes(DELTA)

    assert entra.token_forms() == [
        {
            "grant_type": ["refresh_token"],
            "client_id": ["app-a"],
            "refresh_token": ["refresh-1"],
            "scope": [SCOPE],
        }
    ]
    graph = [r.headers["Authorization"] for r in entra.requests if str(r.url) == DELTA]
    assert graph == ["Bearer access-1", "Bearer access-1"]
    assert path.read_text() == "refresh-2"


async def test_an_expired_login_makes_the_library_unavailable_and_says_to_sign_in_again(
    tmp_path: Path,
) -> None:
    refused = {"error": "invalid_grant", "error_description": "AADSTS70043: refresh-1 expired"}
    path = stored(tmp_path / "token", "refresh-1")

    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(Entra(httpx.Response(400, json=refused)), path).changes(DELTA)

    assert str(raised.value) == f"sharepoint-rh: Graph answered 400 invalid_grant; {RELOGIN}"
    assert path.read_text() == "refresh-1"


async def test_without_a_login_the_library_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(Entra(), tmp_path / "missing").changes(DELTA)

    assert str(raised.value) == f"sharepoint-rh: FileNotFoundError; {RELOGIN}"


async def test_a_token_file_it_cannot_rewrite_makes_the_library_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def read_only(path: Path, refresh: str) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr("pxos_sharepoint.credentials.save", read_only)
    path = stored(tmp_path / "token", "refresh-1")

    with pytest.raises(ConnectorUnavailable) as raised:
        await connector(Entra(granted("access-1", "refresh-2")), path).changes(DELTA)

    assert str(raised.value) == "sharepoint-rh: PermissionError keeping the rotated token"
