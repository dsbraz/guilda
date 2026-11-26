"""What the deploy configures for the bot, through the distribution: never hardcoded."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Certificate:
    """The bot's certificate credential: its private key and the SHA-1 thumbprint Entra
    shows for the uploaded certificate."""

    private_key_pem: str
    thumbprint: str


@dataclass(frozen=True)
class Settings:
    app_id: str
    """The Azure Bot's Microsoft App ID (single tenant)."""
    tenant_id: str
    service_url: str
    """The Bot Connector of the tenant's region, e.g. ``https://smba.trafficmanager.net/teams/``."""
    threads: Mapping[str, str] = field(default_factory=dict)
    """Each kernel thread reference to its Teams conversation: a channel id, where each
    post starts a reply chain, or the id of one reply chain."""
    secret: str | None = None
    certificate: Certificate | None = None
