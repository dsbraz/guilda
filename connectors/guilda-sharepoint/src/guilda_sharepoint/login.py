"""Sign in once to read SharePoint as yourself, for development (DelegatedToken).

The device code flow of the Microsoft identity platform: it shows the page and the code,
waits while you sign in in a browser, and writes the refresh token to the token file,
readable only by you. It needs an Entra app with public client flows allowed and the
delegated ``Files.Read.All`` permission, consented by you if the tenant allows.

With ``--link``, a folder's link as SharePoint copies it, it then resolves the link through
the shares API, with the token just issued, and prints the drive and folder to configure.
A link it cannot resolve fails the command but keeps the login. Never prints a token.
"""

import argparse
import asyncio
import base64
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from guilda_sharepoint.connector import GRAPH, _error_code
from guilda_sharepoint.credentials import DELEGATED_SCOPE, save

_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"
_SLOW_DOWN = 5


class Refused(Exception):
    """The service refused the request; only its status and error code."""


async def sign_in(
    tenant: str,
    client_id: str,
    path: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Keeps the refresh token in ``path`` and returns the access token."""
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    async with httpx.AsyncClient(timeout=60, transport=transport) as client:
        answer = await client.post(
            f"{base}/devicecode", data={"client_id": client_id, "scope": DELEGATED_SCOPE}
        )
        device = _ok(answer).json()
        print(device["message"], flush=True)
        interval = device["interval"]
        form = {
            "grant_type": _DEVICE_CODE,
            "client_id": client_id,
            "device_code": device["device_code"],
        }
        while True:
            await sleep(interval)
            answer = await client.post(f"{base}/token", data=form)
            error = None if answer.is_success else _error_code(answer)
            if error == "slow_down":
                interval += _SLOW_DOWN
            if error not in ("authorization_pending", "slow_down"):
                break
    tokens = _ok(answer).json()
    save(path, tokens["refresh_token"])
    return str(tokens["access_token"])


async def resolve(
    link: str, access: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, str]:
    """The drive and item ids of a sharing link, encoded as the shares API specifies."""
    encoded = base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=60, transport=transport) as client:
        answer = await client.get(
            f"{GRAPH}/shares/u!{encoded}/driveItem",
            headers={"Authorization": f"Bearer {access}"},
        )
    item = _ok(answer).json()
    return item["parentReference"]["driveId"], item["id"]


def _ok(answer: httpx.Response) -> httpx.Response:
    if answer.is_success:
        return answer
    code = _error_code(answer)
    raise Refused(f"{answer.status_code}{f' {code}' if code else ''}")


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="the tenant id or domain")
    parser.add_argument("--client-id", required=True, help="the Entra app's client id")
    parser.add_argument("--token-path", required=True, type=Path, help="the token file")
    parser.add_argument("--link", help="a folder's link, to print its drive and folder ids")
    args = parser.parse_args(argv)
    try:
        access = asyncio.run(
            sign_in(args.tenant, args.client_id, args.token_path, transport=transport, sleep=sleep)
        )
    except Refused as error:
        print(f"login failed: {error}", file=sys.stderr)
        return 1
    print(f"signed in; the refresh token is in {args.token_path}")
    if args.link is None:
        return 0
    try:
        drive, folder = asyncio.run(resolve(args.link, access, transport=transport))
    except Refused as error:
        print(f"could not resolve the link: {error}", file=sys.stderr)
        return 1
    print(f"GUILDA_SHAREPOINT_DRIVE_ID={drive}\nGUILDA_SHAREPOINT_FOLDER_ID={folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
