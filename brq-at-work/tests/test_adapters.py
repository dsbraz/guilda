"""BRQ@work wires SharePoint, Granola and Teams from the deploy's environment.

Every connector delivers its sources under the distro's access tags; the Teams channel
maps the assistant's thread to the configured conversation and keeps the message of each
card in a durable shelf. A missing variable stops the installation, naming it. No call
leaves the process: Microsoft is a MockTransport.
"""

import datetime
import shelve
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from brq_at_work.composition import ACCESS, IDENTITIES
from brq_at_work.environment import MissingSetting, identities, sharepoint, teams
from brq_at_work.publish_agent import AGENT_FILE
from brq_at_work.serve import AGENT, adapters
from guilda_granola import GranolaTranscripts
from guilda_kernel.channel import Card
from guilda_kernel.identity import read_identities
from guilda_kernel.meetings import MeetingConnector
from guilda_kernel.ontology import Registry
from guilda_sharepoint import ClientCertificate, ClientSecret, DelegatedToken, SharePointConnector
from guilda_teams import Certificate

CONVERSATION = "19:project@thread.tacv2"
REQUIRED = {
    "GUILDA_SHAREPOINT_TENANT": "tenant-sp",
    "GUILDA_SHAREPOINT_CLIENT_ID": "client-sp",
    "GUILDA_SHAREPOINT_DRIVE_ID": "drive-1",
    "GUILDA_GRANOLA_API_KEY": "grn_key",
    "GUILDA_TEAMS_APP_ID": "app-1",
    "GUILDA_TEAMS_TENANT_ID": "tenant-teams",
    "GUILDA_TEAMS_SERVICE_URL": "https://smba.example.test/teams/",
    "GUILDA_TEAMS_CONVERSATION": CONVERSATION,
}
SECRETS = {"GUILDA_SHAREPOINT_CLIENT_SECRET": "sp-secret", "GUILDA_TEAMS_SECRET": "bot-secret"}


def environment(cards: Path, **overrides: str) -> dict[str, str]:
    return {**REQUIRED, **SECRETS, "GUILDA_TEAMS_CARDS_PATH": str(cards), **overrides}


class Microsoft:
    """Entra's token endpoint and the Bot Connector: every call gets a new id."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json={"id": f"id-{uuid4()}", "access_token": "tok", "expires_in": 3600}
        )

    def posted(self) -> list[str]:
        return [str(r.url) for r in self.requests if "/v3/conversations" in str(r.url)]


def card() -> Card:
    return Card(uuid4(), "ana", "pkg", "action", "Report", {}, "quote", "source", "digest")


def pem_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "guilda")])
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return private.decode(), certificate.public_bytes(serialization.Encoding.PEM).decode()


async def test_every_connector_delivers_under_the_distros_access_tags(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    async with adapters(
        pool, registry, read_identities(IDENTITIES), environment(tmp_path / "cards")
    ) as wired:
        library, meetings = wired.connectors

    assert isinstance(library, SharePointConnector) and library.name == "sharepoint"
    assert library._access == ACCESS
    assert isinstance(meetings, MeetingConnector) and meetings.name == "granola"
    assert isinstance(meetings._source, GranolaTranscripts)
    assert meetings._source._access == ACCESS and meetings._agent == AGENT


async def test_the_assistants_thread_posts_to_the_configured_conversation(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    microsoft = Microsoft()
    thread = yaml.safe_load(AGENT_FILE.read_text(encoding="utf-8"))["thread"]
    async with adapters(
        pool,
        registry,
        read_identities(IDENTITIES),
        environment(tmp_path / "cards"),
        transport=httpx.MockTransport(microsoft),
    ) as wired:
        await wired.channel.post(thread, "Alguém sabe se a entrega saiu?")

    assert microsoft.posted() == [
        "https://smba.example.test/teams/v3/conversations/19%3Aproject%40thread.tacv2/activities"
    ]


async def test_a_card_delivered_again_after_a_restart_replaces_its_message(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    microsoft, review = Microsoft(), card()
    env = environment(tmp_path / "cards")
    for _ in range(2):
        async with adapters(
            pool,
            registry,
            read_identities(IDENTITIES),
            env,
            transport=httpx.MockTransport(microsoft),
        ) as wired:
            await wired.channel.deliver_card(review)

    methods = [r.method for r in microsoft.requests if "/activities" in str(r.url)]
    assert methods == ["POST", "PUT"]
    with shelve.open(env["GUILDA_TEAMS_CARDS_PATH"]) as cards:
        assert list(cards) == [f"{review.proposal_id}:ana"]


async def test_the_bot_endpoint_refuses_an_unauthenticated_call(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path
) -> None:
    async with adapters(
        pool,
        registry,
        read_identities(IDENTITIES),
        environment(tmp_path / "cards"),
        transport=httpx.MockTransport(Microsoft()),
    ) as wired:
        assert wired.app is not None
        transport = httpx.ASGITransport(app=wired.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as http:
            response = await http.post("/api/messages", json={"type": "message"})

    assert response.status_code == 401


@pytest.mark.parametrize("name", [*REQUIRED, "GUILDA_TEAMS_CARDS_PATH"])
async def test_a_missing_variable_stops_the_installation_naming_it(
    pool: asyncpg.Pool, registry: Registry, tmp_path: Path, name: str
) -> None:
    unset = environment(tmp_path / "cards")
    del unset[name]
    for env in (unset, environment(tmp_path / "cards", **{name: " "})):
        with pytest.raises(MissingSetting, match=name):
            async with adapters(pool, registry, read_identities(IDENTITIES), env):
                pass


@pytest.mark.parametrize(
    ("secret", "key"),
    [
        ("GUILDA_SHAREPOINT_CLIENT_SECRET", "GUILDA_SHAREPOINT_CERTIFICATE_KEY"),
        ("GUILDA_TEAMS_SECRET", "GUILDA_TEAMS_CERTIFICATE_KEY"),
    ],
)
def test_a_credential_is_a_secret_or_a_certificate_never_both_or_neither(
    tmp_path: Path, secret: str, key: str
) -> None:
    neither = environment(tmp_path, **{secret: ""})
    both = environment(tmp_path, **{key: "-----BEGIN PRIVATE KEY-----"})
    for env in (neither, both):
        with pytest.raises(MissingSetting, match=f"{secret}.*{key}"):
            if "SHAREPOINT" in secret:
                sharepoint(env)
            else:
                teams(env, "projeto-piloto")


def test_sharepoint_authenticates_with_a_certificate_when_one_is_given(tmp_path: Path) -> None:
    key, certificate = pem_pair()
    env = environment(
        tmp_path,
        GUILDA_SHAREPOINT_CLIENT_SECRET="",
        GUILDA_SHAREPOINT_CERTIFICATE_KEY=key,
        GUILDA_SHAREPOINT_FOLDER_ID="folder-9",
    )
    with pytest.raises(MissingSetting, match="GUILDA_SHAREPOINT_CERTIFICATE\\b"):
        sharepoint(env)

    library = sharepoint({**env, "GUILDA_SHAREPOINT_CERTIFICATE": certificate})

    assert library._credential == ClientCertificate(key.encode(), certificate.encode())
    assert library._start.endswith("/drives/drive-1/items/folder-9/delta")
    assert sharepoint(environment(tmp_path))._credential == ClientSecret("sp-secret")


def test_sharepoint_reads_as_a_signed_in_user_when_a_token_path_is_given(tmp_path: Path) -> None:
    token = str(tmp_path / "sharepoint-token")
    env = environment(tmp_path, GUILDA_SHAREPOINT_CLIENT_SECRET="")

    library = sharepoint({**env, "GUILDA_SHAREPOINT_TOKEN_PATH": token})

    assert library._credential == DelegatedToken(Path(token))
    for other in (
        {"GUILDA_SHAREPOINT_CLIENT_SECRET": "sp-secret"},
        {"GUILDA_SHAREPOINT_CERTIFICATE_KEY": "-----BEGIN PRIVATE KEY-----"},
    ):
        with pytest.raises(MissingSetting, match="GUILDA_SHAREPOINT_TOKEN_PATH"):
            sharepoint({**env, "GUILDA_SHAREPOINT_TOKEN_PATH": token, **other})


def test_teams_authenticates_with_a_certificate_when_one_is_given(tmp_path: Path) -> None:
    env = environment(tmp_path, GUILDA_TEAMS_SECRET="", GUILDA_TEAMS_CERTIFICATE_KEY="pem")
    with pytest.raises(MissingSetting, match="GUILDA_TEAMS_CERTIFICATE_THUMBPRINT"):
        teams(env, "projeto-piloto")

    settings = teams({**env, "GUILDA_TEAMS_CERTIFICATE_THUMBPRINT": "ab12"}, "projeto-piloto")

    assert (settings.secret, settings.certificate) == (None, Certificate("pem", "ab12"))
    assert settings.threads == {"projeto-piloto": CONVERSATION}


def test_identities_come_from_the_distro_unless_the_deploy_points_elsewhere(
    tmp_path: Path,
) -> None:
    own = tmp_path / "identities.yaml"
    assert identities({}) == IDENTITIES
    assert identities({"GUILDA_IDENTITIES_PATH": " "}) == IDENTITIES
    assert identities({"GUILDA_IDENTITIES_PATH": str(own)}) == own
