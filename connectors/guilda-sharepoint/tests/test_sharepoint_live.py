"""The connector reads a real document library. Opt-in: real calls to the tenant.

Run with GUILDA_SHAREPOINT_TENANT, GUILDA_SHAREPOINT_CLIENT_ID, GUILDA_SHAREPOINT_CLIENT_SECRET
and GUILDA_SHAREPOINT_DRIVE_ID, and optionally GUILDA_SHAREPOINT_FOLDER_ID: an Entra app
granted Sites.Selected (read) on the library's site, as a deploy would configure it. Or,
in place of the secret, GUILDA_SHAREPOINT_TOKEN_PATH, a token file written by
``python -m guilda_sharepoint.login``, to read as that user. Use a library holding a few
test files; the pass reads every one.
"""

import os
from pathlib import Path

import pytest

from guilda_sharepoint import ClientSecret, DelegatedToken, SharePointConnector

TENANT = os.environ.get("GUILDA_SHAREPOINT_TENANT", "")
pytestmark = pytest.mark.skipif(not TENANT, reason="GUILDA_SHAREPOINT_TENANT not set")


async def test_a_full_pass_then_a_pass_from_its_cursor() -> None:
    drive = os.environ["GUILDA_SHAREPOINT_DRIVE_ID"]
    token = os.environ.get("GUILDA_SHAREPOINT_TOKEN_PATH")
    sharepoint = SharePointConnector(
        "sharepoint-live",
        tenant=TENANT,
        client_id=os.environ["GUILDA_SHAREPOINT_CLIENT_ID"],
        credential=DelegatedToken(Path(token))
        if token
        else ClientSecret(os.environ["GUILDA_SHAREPOINT_CLIENT_SECRET"]),
        drive_id=drive,
        folder_id=os.environ.get("GUILDA_SHAREPOINT_FOLDER_ID") or None,
        access=("corporate",),
    )

    first = await sharepoint.changes(None)
    later = await sharepoint.changes(first.cursor)

    assert first.cursor.startswith("https://graph.microsoft.com/")
    assert first.documents, "the library should hold at least one supported file"
    for document in first.documents:
        assert document.source.startswith(f"sharepoint-live:{drive}:")
        assert document.version and document.access == ("corporate",)
        assert "\nhttps://" in document.text
    assert later.cursor.startswith("https://graph.microsoft.com/")
