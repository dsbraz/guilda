"""What the deploy configures for each adapter, read from its environment (.env.example).

SharePoint, Granola and Teams are all required in production: a variable missing, or
set empty as ``.env.example`` leaves it, stops the installation with ``MissingSetting``
naming it. ``GUILDA_CHANNEL=telegram`` takes Telegram in place of Teams, for local
development only. Each Entra app authenticates with a secret or a certificate, never both;
for development only, SharePoint can instead read as a user signed in with
``python -m guilda_sharepoint.login``, one credential of the three.
Every connector delivers its sources under the distro's access tags (``ACCESS``).
"""

from collections.abc import Mapping
from pathlib import Path

from brq_at_work.composition import ACCESS, IDENTITIES
from guilda_granola import GranolaTranscripts
from guilda_kernel.errors import GuildaError
from guilda_sharepoint import ClientCertificate, ClientSecret, DelegatedToken, SharePointConnector
from guilda_teams import Certificate, Settings
from guilda_telegram import Settings as TelegramSettings

Environment = Mapping[str, str]


class MissingSetting(GuildaError):
    """A variable the installation needs is not set."""


def required(env: Environment, name: str) -> str:
    value = optional(env, name)
    if value is None:
        raise MissingSetting(f"{name} is not set")
    return value


def optional(env: Environment, name: str) -> str | None:
    value = env.get(name, "")
    return value if value.strip() else None


def identities(env: Environment) -> Path:
    """The people and their channel ids: the distro's file, or the deploy's own, which
    keeps real ids out of the repository."""
    path = optional(env, "GUILDA_IDENTITIES_PATH")
    return IDENTITIES if path is None else Path(path)


def _secret_or_key(env: Environment, secret: str, key: str) -> tuple[str | None, str | None]:
    pair = optional(env, secret), optional(env, key)
    if (pair[0] is None) == (pair[1] is None):
        raise MissingSetting(f"set either {secret} or {key}, not both")
    return pair


_SHAREPOINT_CREDENTIALS = (
    "GUILDA_SHAREPOINT_CLIENT_SECRET",
    "GUILDA_SHAREPOINT_CERTIFICATE_KEY",
    "GUILDA_SHAREPOINT_TOKEN_PATH",
)


def sharepoint(env: Environment) -> SharePointConnector:
    """The corporate document library; for development, read as a signed-in user."""
    secret, key, token = (optional(env, name) for name in _SHAREPOINT_CREDENTIALS)
    if [secret, key, token].count(None) != 2:
        raise MissingSetting(f"set exactly one of {', '.join(_SHAREPOINT_CREDENTIALS)}")
    credential: ClientSecret | ClientCertificate | DelegatedToken
    if token is not None:
        credential = DelegatedToken(Path(token))
    elif key is None:
        credential = ClientSecret(str(secret))
    else:
        certificate = required(env, "GUILDA_SHAREPOINT_CERTIFICATE")
        credential = ClientCertificate(key.encode(), certificate.encode())
    return SharePointConnector(
        "sharepoint",
        tenant=required(env, "GUILDA_SHAREPOINT_TENANT"),
        client_id=required(env, "GUILDA_SHAREPOINT_CLIENT_ID"),
        credential=credential,
        drive_id=required(env, "GUILDA_SHAREPOINT_DRIVE_ID"),
        folder_id=optional(env, "GUILDA_SHAREPOINT_FOLDER_ID"),
        access=ACCESS,
    )


def granola(env: Environment) -> GranolaTranscripts:
    """The workspace's meeting transcripts."""
    return GranolaTranscripts(
        required(env, "GUILDA_GRANOLA_API_KEY"),
        ACCESS,
        space_id=optional(env, "GUILDA_GRANOLA_SPACE_ID"),
        folder_id=optional(env, "GUILDA_GRANOLA_FOLDER_ID"),
    )


def teams(env: Environment, thread: str) -> Settings:
    """The bot, with the kernel's ``thread`` mapped to the project's Teams conversation."""
    secret, key = _secret_or_key(env, "GUILDA_TEAMS_SECRET", "GUILDA_TEAMS_CERTIFICATE_KEY")
    certificate = None
    if key is not None:
        certificate = Certificate(key, required(env, "GUILDA_TEAMS_CERTIFICATE_THUMBPRINT"))
    return Settings(
        app_id=required(env, "GUILDA_TEAMS_APP_ID"),
        tenant_id=required(env, "GUILDA_TEAMS_TENANT_ID"),
        service_url=required(env, "GUILDA_TEAMS_SERVICE_URL"),
        threads={thread: required(env, "GUILDA_TEAMS_CONVERSATION")},
        secret=secret,
        certificate=certificate,
    )


def channel(env: Environment) -> str:
    """``GUILDA_CHANNEL``: ``teams`` (the default) or ``telegram``, for local development."""
    name = optional(env, "GUILDA_CHANNEL") or "teams"
    if name not in {"teams", "telegram"}:
        raise MissingSetting(f"GUILDA_CHANNEL is {name!r}; set teams or telegram")
    return name


def telegram(env: Environment, thread: str) -> TelegramSettings:
    """The bot, with the kernel's ``thread`` in the project's group (and topic)."""
    topic = optional(env, "GUILDA_TELEGRAM_TOPIC_ID")
    return TelegramSettings(
        bot_token=required(env, "GUILDA_TELEGRAM_BOT_TOKEN"),
        chat_id=int(required(env, "GUILDA_TELEGRAM_CHAT_ID")),
        thread=thread,
        topic_id=None if topic is None else int(topic),
    )
