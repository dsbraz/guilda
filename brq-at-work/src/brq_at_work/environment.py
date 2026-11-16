"""What the deploy configures for each adapter, read from its environment (.env.example).

SharePoint, Granola and Teams are all required in production: a variable missing, or
set empty as ``.env.example`` leaves it, stops the installation with ``MissingSetting``
naming it. Each Entra app authenticates with a secret or a certificate, never both; for
development only, SharePoint can instead read as a user signed in with
``python -m pxos_sharepoint.login``, one credential of the three.
Every connector delivers its sources under the distro's access tags (``ACCESS``).
"""

from collections.abc import Mapping
from pathlib import Path

from brq_at_work.composition import ACCESS
from pxos_granola import GranolaTranscripts
from pxos_kernel.errors import PxosError
from pxos_sharepoint import ClientCertificate, ClientSecret, DelegatedToken, SharePointConnector
from pxos_teams import Certificate, Settings

Environment = Mapping[str, str]


class MissingSetting(PxosError):
    """A variable the installation needs is not set."""


def required(env: Environment, name: str) -> str:
    value = optional(env, name)
    if value is None:
        raise MissingSetting(f"{name} is not set")
    return value


def optional(env: Environment, name: str) -> str | None:
    value = env.get(name, "")
    return value if value.strip() else None


def _secret_or_key(env: Environment, secret: str, key: str) -> tuple[str | None, str | None]:
    pair = optional(env, secret), optional(env, key)
    if (pair[0] is None) == (pair[1] is None):
        raise MissingSetting(f"set either {secret} or {key}, not both")
    return pair


_SHAREPOINT_CREDENTIALS = (
    "PXOS_SHAREPOINT_CLIENT_SECRET",
    "PXOS_SHAREPOINT_CERTIFICATE_KEY",
    "PXOS_SHAREPOINT_TOKEN_PATH",
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
        certificate = required(env, "PXOS_SHAREPOINT_CERTIFICATE")
        credential = ClientCertificate(key.encode(), certificate.encode())
    return SharePointConnector(
        "sharepoint",
        tenant=required(env, "PXOS_SHAREPOINT_TENANT"),
        client_id=required(env, "PXOS_SHAREPOINT_CLIENT_ID"),
        credential=credential,
        drive_id=required(env, "PXOS_SHAREPOINT_DRIVE_ID"),
        folder_id=optional(env, "PXOS_SHAREPOINT_FOLDER_ID"),
        access=ACCESS,
    )


def granola(env: Environment) -> GranolaTranscripts:
    """The workspace's meeting transcripts."""
    return GranolaTranscripts(
        required(env, "PXOS_GRANOLA_API_KEY"),
        ACCESS,
        space_id=optional(env, "PXOS_GRANOLA_SPACE_ID"),
        folder_id=optional(env, "PXOS_GRANOLA_FOLDER_ID"),
    )


def teams(env: Environment, thread: str) -> Settings:
    """The bot, with the kernel's ``thread`` mapped to the project's Teams conversation."""
    secret, key = _secret_or_key(env, "PXOS_TEAMS_SECRET", "PXOS_TEAMS_CERTIFICATE_KEY")
    certificate = None
    if key is not None:
        certificate = Certificate(key, required(env, "PXOS_TEAMS_CERTIFICATE_THUMBPRINT"))
    return Settings(
        app_id=required(env, "PXOS_TEAMS_APP_ID"),
        tenant_id=required(env, "PXOS_TEAMS_TENANT_ID"),
        service_url=required(env, "PXOS_TEAMS_SERVICE_URL"),
        threads={thread: required(env, "PXOS_TEAMS_CONVERSATION")},
        secret=secret,
        certificate=certificate,
    )
