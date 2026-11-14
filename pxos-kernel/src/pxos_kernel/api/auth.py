"""Bearer tokens from the installation's OIDC identity provider (PXOS-57).

The verifier only names the caller; whether that actor exists, and whether an agent may
act for the person, is decided by the database when the session opens (PXOS-50).
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken

ALGORITHMS = ["RS256", "ES256", "PS256", "EdDSA"]
"""Asymmetric only: a shared secret or an unsigned token never names an actor."""


@dataclass(frozen=True)
class Caller:
    actor: str
    on_behalf_of: str | None = None

    @classmethod
    def from_token(cls, access: AccessToken) -> "Caller":
        """The subject is the actor; with RFC 8693 ``act``, the agent acts for the subject."""
        assert access.subject is not None
        act = (access.claims or {}).get("act")
        if isinstance(act, dict) and isinstance(act.get("sub"), str):
            return cls(actor=act["sub"], on_behalf_of=access.subject)
        return cls(actor=access.subject)


class JwtVerifier:
    """Checks signature, issuer, audience and expiry against a JWKS."""

    def __init__(self, *, issuer: str, audience: str, jwks: dict[str, Any]) -> None:
        self._issuer = issuer
        self._audience = audience
        self._keys = jwt.PyJWKSet.from_dict(jwks)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = self._keys[str(jwt.get_unverified_header(token).get("kid"))]
            claims = jwt.decode(
                token,
                key,
                algorithms=ALGORITHMS,
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, KeyError):
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims["sub"]),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            subject=str(claims["sub"]),
            claims=claims,
        )


class LocalIssuer:
    """Development identity provider: one RSA key that signs tokens for any actor."""

    def __init__(self, private_key: rsa.RSAPrivateKey, *, issuer: str, audience: str) -> None:
        self._key = private_key
        self.issuer = issuer
        self.audience = audience
        public = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.kid = str(uuid.uuid5(uuid.NAMESPACE_OID, public.hex()))

    @classmethod
    def generate(cls, *, issuer: str, audience: str) -> "LocalIssuer":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return cls(key, issuer=issuer, audience=audience)

    @classmethod
    def from_pem(cls, pem: bytes, *, issuer: str, audience: str) -> "LocalIssuer":
        key = serialization.load_pem_private_key(pem, password=None)
        assert isinstance(key, rsa.RSAPrivateKey)
        return cls(key, issuer=issuer, audience=audience)

    def pem(self) -> bytes:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        return {"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})

    def token(self, person: str, *, agent: str | None = None, ttl: int = 3600) -> str:
        """A token for ``person``, or for ``agent`` acting on behalf of ``person``."""
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": person,
            "exp": int(time.time()) + ttl,
        }
        if agent is not None:
            claims["act"] = {"sub": agent}
        return self.sign(claims)
