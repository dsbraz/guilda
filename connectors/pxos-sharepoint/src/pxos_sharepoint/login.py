"""Sign in once to read SharePoint as yourself, for development (DelegatedToken).

The device code flow of the Microsoft identity platform: it shows the page and the code,
waits while you sign in in a browser, and writes the refresh token to the token file,
readable only by you. It needs an Entra app with public client flows allowed and the
delegated ``Files.Read.All`` permission, consented by you if the tenant allows. Never
prints a token.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from pxos_sharepoint.connector import _error_code
from pxos_sharepoint.credentials import DELEGATED_SCOPE, save

_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"
_SLOW_DOWN = 5


class LoginFailed(Exception):
    """The identity platform refused the login; only its status and error code."""


async def sign_in(
    tenant: str,
    client_id: str,
    path: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
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
    save(path, _ok(answer).json()["refresh_token"])


def _ok(answer: httpx.Response) -> httpx.Response:
    if answer.is_success:
        return answer
    code = _error_code(answer)
    raise LoginFailed(f"{answer.status_code}{f' {code}' if code else ''}")


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
    args = parser.parse_args(argv)
    try:
        asyncio.run(
            sign_in(args.tenant, args.client_id, args.token_path, transport=transport, sleep=sleep)
        )
    except LoginFailed as error:
        print(f"login failed: {error}", file=sys.stderr)
        return 1
    print(f"signed in; the refresh token is in {args.token_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
