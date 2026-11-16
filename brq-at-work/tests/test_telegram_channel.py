"""PXOS_CHANNEL picks the channel: Teams by default, Telegram for local development.

With Telegram the assistant's thread is the configured group (and topic), the message of
each card is kept in a durable shelf, and no Teams variable is needed. A missing
variable, or a channel the distro does not know, stops the installation, naming it. No
call leaves the process: the Bot API is a MockTransport.
"""

import json
import shelve
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import httpx
import pytest
import yaml

from brq_at_work.composition import IDENTITIES
from brq_at_work.environment import MissingSetting
from brq_at_work.publish_agent import AGENT_FILE
from brq_at_work.serve import adapters
from pxos_kernel.channel import Card
from pxos_kernel.identity import Actor, Identities, read_identities
from pxos_kernel.ontology import Registry
from pxos_teams import TeamsChannel
from pxos_telegram import TelegramChannel

CONNECTORS = {
    "PXOS_SHAREPOINT_TENANT": "tenant-sp",
    "PXOS_SHAREPOINT_CLIENT_ID": "client-sp",
    "PXOS_SHAREPOINT_CLIENT_SECRET": "sp-secret",
    "PXOS_SHAREPOINT_DRIVE_ID": "drive-1",
    "PXOS_GRANOLA_API_KEY": "grn_key",
}
TELEGRAM = {
    "PXOS_CHANNEL": "telegram",
    "PXOS_TELEGRAM_BOT_TOKEN": "123:tok",
    "PXOS_TELEGRAM_CHAT_ID": "-1001234",
}
TEAMS = {
    "PXOS_TEAMS_APP_ID": "app-1",
    "PXOS_TEAMS_TENANT_ID": "tenant-teams",
    "PXOS_TEAMS_SERVICE_URL": "https://smba.example.test/teams/",
    "PXOS_TEAMS_CONVERSATION": "19:project@thread.tacv2",
    "PXOS_TEAMS_SECRET": "bot-secret",
}


def environment(cards: Path, **overrides: str) -> dict[str, str]:
    return {**CONNECTORS, **TELEGRAM, "PXOS_TELEGRAM_CARDS_PATH": str(cards), **overrides}


class Telegram:
    """The Bot API: every message sent gets a new id."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})


async def test_telegram_takes_the_assistants_thread_to_the_configured_group(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    telegram = Telegram()
    thread = yaml.safe_load(AGENT_FILE.read_text(encoding="utf-8"))["thread"]
    env = environment(tmp_path / "cards", PXOS_TELEGRAM_TOPIC_ID="7")
    async with adapters(
        pool, registry, read_identities(IDENTITIES), env, transport=httpx.MockTransport(telegram)
    ) as wired:
        assert isinstance(wired.channel, TelegramChannel) and wired.app is not None
        await wired.channel.post(thread, "Alguém sabe se a entrega saiu?")

    assert telegram.calls == [
        (
            "sendMessage",
            {"chat_id": -1001234, "message_thread_id": 7, "text": "Alguém sabe se a entrega saiu?"},
        )
    ]


async def test_a_card_delivered_again_after_a_restart_edits_its_message(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    telegram = Telegram()
    review = Card(uuid4(), "ana", "pkg", "action", "Report", {}, "quote", "source", "digest")
    identities = Identities(
        actors=(Actor(id="ana", kind="internal_person", name="Ana", channel_ids=("telegram:42",)),)
    )
    env = environment(tmp_path / "cards")
    for title in ["Report", "Report (revisto)"]:
        async with adapters(
            pool, registry, identities, env, transport=httpx.MockTransport(telegram)
        ) as wired:
            await wired.channel.deliver_card(replace(review, title=title))

    assert [method for method, _ in telegram.calls] == ["sendMessage", "editMessageText"]
    with shelve.open(env["PXOS_TELEGRAM_CARDS_PATH"]) as cards:
        assert list(cards) == [f"{review.proposal_id}:42"]


@pytest.mark.parametrize(
    "name", ["PXOS_TELEGRAM_BOT_TOKEN", "PXOS_TELEGRAM_CHAT_ID", "PXOS_TELEGRAM_CARDS_PATH"]
)
async def test_a_missing_telegram_variable_stops_the_installation_naming_it(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path, name: str
) -> None:
    unset = environment(tmp_path / "cards")
    del unset[name]
    for env in (unset, environment(tmp_path / "cards", **{name: " "})):
        with pytest.raises(MissingSetting, match=name):
            async with adapters(pool, registry, read_identities(IDENTITIES), env):
                pass


@pytest.mark.parametrize("channel", ["", "teams"], ids=["default", "explicit"])
async def test_teams_is_the_channel_unless_telegram_is_chosen(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path, channel: str
) -> None:
    env = {
        **CONNECTORS,
        **TEAMS,
        "PXOS_CHANNEL": channel,
        "PXOS_TEAMS_CARDS_PATH": str(tmp_path / "cards"),
    }
    async with adapters(pool, registry, read_identities(IDENTITIES), env) as wired:
        assert isinstance(wired.channel, TeamsChannel)


async def test_an_unknown_channel_stops_the_installation_naming_it(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    env = environment(tmp_path / "cards", PXOS_CHANNEL="slack")
    with pytest.raises(MissingSetting, match="PXOS_CHANNEL"):
        async with adapters(pool, registry, read_identities(IDENTITIES), env):
            pass
