"""The Entra app's credential in the client credentials grant, or a user's for development.

A secret is sent as is. A certificate signs a short-lived client assertion, as the Microsoft
identity platform's certificate credentials specify: PS256, the SHA-256 thumbprint of the
certificate in ``x5t#S256``, and the token endpoint as audience. Neither shows its secret
in its representation.

A delegated token reads as a signed-in user, only what they can see: a public client trades
the refresh token that ``python -m guilda_sharepoint.login`` kept in a file, read at each
request so a new login needs no restart, and keeps the rotated one in the same file.
"""

import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_LIFETIME = 600
DELEGATED_SCOPE = "https://graph.microsoft.com/Files.Read.All offline_access"
LOGIN = "run python -m guilda_sharepoint.login again"


@dataclass(frozen=True)
class ClientSecret:
    secret: str = field(repr=False)

    def form(self, client_id: str, audience: str) -> dict[str, str]:
        """The token request fields that authenticate the app."""
        return {"client_secret": self.secret}


@dataclass(frozen=True)
class ClientCertificate:
    private_key: bytes = field(repr=False)
    """The RSA private key, PEM, unencrypted."""
    certificate: bytes
    """The X.509 certificate registered on the app, PEM."""

    def __post_init__(self) -> None:
        self._key()

    def _key(self) -> rsa.RSAPrivateKey:
        key = serialization.load_pem_private_key(self.private_key, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("the certificate key must be RSA")
        return key

    def form(self, client_id: str, audience: str) -> dict[str, str]:
        """The token request fields that authenticate the app: a signed client assertion."""
        der = x509.load_pem_x509_certificate(self.certificate).public_bytes(
            serialization.Encoding.DER
        )
        header = {"alg": "PS256", "typ": "JWT", "x5t#S256": _b64(hashlib.sha256(der).digest())}
        now = int(time.time())
        claims = {
            "aud": audience,
            "iss": client_id,
            "sub": client_id,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + _LIFETIME,
        }
        signed = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
        signature = self._key().sign(
            signed.encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256.digest_size),
            hashes.SHA256(),
        )
        return {
            "client_assertion_type": _ASSERTION_TYPE,
            "client_assertion": f"{signed}.{_b64(signature)}",
        }


@dataclass(frozen=True)
class DelegatedToken:
    path: Path
    """The file the login keeps the user's refresh token in."""

    def form(self, client_id: str, audience: str) -> dict[str, str]:
        """The refresh token grant's fields; they override the client credentials grant's."""
        refresh = self.path.read_text().strip()
        return {"grant_type": "refresh_token", "refresh_token": refresh, "scope": DELEGATED_SCOPE}

    def keep(self, granted: Mapping[str, Any]) -> None:
        """Keeps the rotated refresh token, if the service issued one."""
        if refresh := granted.get("refresh_token"):
            save(self.path, refresh)


Credential = ClientSecret | ClientCertificate | DelegatedToken


def save(path: Path, refresh: str) -> None:
    """Writes the refresh token in place (a bind-mounted file stays mounted), owner-only."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(refresh)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
