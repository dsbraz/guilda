"""A user signs in once, by the device code flow, and the refresh token goes to a file.

The fake identity platform answers as the device code flow page documents: the device
authorization response, then the token endpoint's ``authorization_pending`` until the user
finishes, ``slow_down`` (RFC 8628) when polled too often, and the tokens, or
``expired_token`` and ``authorization_declined``.
"""

import base64
import stat
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from pxos_sharepoint.login import main

BASE = "https://login.microsoftonline.com/tenant-a/oauth2/v2.0"
SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"
MESSAGE = (
    "To sign in, use a web browser to open the page https://microsoft.com/devicelogin "
    "and enter the code FJ5CHVD8T to authenticate."
)
DEVICE = {
    "device_code": "device-1",
    "user_code": "FJ5CHVD8T",
    "verification_uri": "https://microsoft.com/devicelogin",
    "expires_in": 900,
    "interval": 5,
    "message": MESSAGE,
}
TOKENS = {
    "token_type": "Bearer",
    "scope": SCOPE,
    "expires_in": 3599,
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "id_token": "id-1",
}


GRAPH = "https://graph.microsoft.com/v1.0"
LINK = "https://contoso.sharepoint.com/:f:/s/rh/EjE5aBcD-pasta?e=x1Y2z3"
SHARE_ID = "u!" + base64.b64encode(LINK.encode()).decode().rstrip("=").replace("/", "_").replace(
    "+", "-"
)
FOLDER = {
    "id": "01FOLDER",
    "name": "Políticas",
    "folder": {"childCount": 3},
    "webUrl": "https://contoso.sharepoint.com/sites/rh/Documentos/Politicas",
    "parentReference": {"driveId": "b!drive-a", "driveType": "documentLibrary", "id": "01ROOT"},
}


def pending(error: str) -> httpx.Response:
    return httpx.Response(400, json={"error": error, "error_description": "AADSTS70016"})


class Entra:
    def __init__(
        self,
        *polls: httpx.Response,
        device: httpx.Response | None = None,
        share: httpx.Response | None = None,
    ) -> None:
        self.device = device or httpx.Response(200, json=DEVICE)
        self.polls = list(polls)
        self.share = share
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == f"{BASE}/devicecode":
            return self.device
        if self.share and str(request.url) == f"{GRAPH}/shares/{SHARE_ID}/driveItem":
            return self.share
        assert str(request.url) == f"{BASE}/token"
        return self.polls.pop(0)


def run(entra: Entra, path: Path, waits: list[float], *more: str) -> int:
    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    argv = ["--tenant", "tenant-a", "--client-id", "app-a", "--token-path", str(path), *more]
    return main(argv, transport=httpx.MockTransport(entra), sleep=sleep)


def test_the_user_signs_in_by_device_code_and_the_refresh_token_is_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "token"
    entra = Entra(
        pending("authorization_pending"),
        pending("slow_down"),
        httpx.Response(200, json=TOKENS),
    )
    waits: list[float] = []

    assert run(entra, path, waits) == 0

    device, *polls = [parse_qs(r.content.decode()) for r in entra.requests]
    assert device == {"client_id": ["app-a"], "scope": [SCOPE]}
    assert polls == 3 * [
        {
            "grant_type": ["urn:ietf:params:oauth:grant-type:device_code"],
            "client_id": ["app-a"],
            "device_code": ["device-1"],
        }
    ]
    assert waits == [5, 5, 10]
    assert path.read_text() == "refresh-1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert MESSAGE in out and str(path) in out
    assert not any(token in out for token in ("access-1", "refresh-1", "id-1", "device-1"))


@pytest.mark.parametrize("error", ["expired_token", "authorization_declined"])
def test_a_login_that_expires_or_is_declined_fails_and_keeps_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], error: str
) -> None:
    path = tmp_path / "token"

    assert run(Entra(pending("authorization_pending"), pending(error)), path, []) == 1

    assert not path.exists()
    assert f"login failed: 400 {error}" in capsys.readouterr().err


def test_a_refused_device_authorization_fails_at_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = {"error": "invalid_request", "error_description": "AADSTS900144: client_id"}
    entra = Entra(device=httpx.Response(400, json=refused))

    assert run(entra, tmp_path / "token", []) == 1

    assert len(entra.requests) == 1
    assert "login failed: 400 invalid_request" in capsys.readouterr().err


def test_a_folder_link_gives_the_drive_and_folder_to_configure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entra = Entra(httpx.Response(200, json=TOKENS), share=httpx.Response(200, json=FOLDER))

    assert run(entra, tmp_path / "token", [], "--link", LINK) == 0

    share = entra.requests[-1]
    assert (share.method, str(share.url)) == ("GET", f"{GRAPH}/shares/{SHARE_ID}/driveItem")
    assert share.headers["Authorization"] == "Bearer access-1"
    out = capsys.readouterr().out
    assert "PXOS_SHAREPOINT_DRIVE_ID=b!drive-a\nPXOS_SHAREPOINT_FOLDER_ID=01FOLDER\n" in out
    assert "access-1" not in out


def test_a_link_it_cannot_resolve_fails_but_keeps_the_login(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    denied = {"error": {"code": "accessDenied", "message": "Access denied to EjE5aBcD"}}
    path = tmp_path / "token"
    entra = Entra(httpx.Response(200, json=TOKENS), share=httpx.Response(403, json=denied))

    assert run(entra, path, [], "--link", LINK) == 1

    assert path.read_text() == "refresh-1"
    captured = capsys.readouterr()
    assert str(path) in captured.out
    assert "could not resolve the link: 403 accessDenied" in captured.err
    assert "EjE5aBcD" not in captured.err
