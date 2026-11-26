"""The app's credential in the client credentials grant of the Microsoft identity platform.

A secret goes as ``client_secret``; a certificate signs a client assertion as the
certificate credentials page specifies: PS256, ``x5t#S256`` and the token endpoint as
audience.
"""

import base64
import datetime
import hashlib
import json
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from guilda_sharepoint.credentials import ClientCertificate, ClientSecret

AUDIENCE = "https://login.microsoftonline.com/tenant-a/oauth2/v2.0/token"


def certificate() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "guilda-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert


def decoded(part: str) -> dict[str, object]:
    return dict(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))))


def test_a_secret_is_sent_as_client_secret_and_kept_out_of_repr() -> None:
    credential = ClientSecret("s3cr3t")

    assert credential.form("app-a", AUDIENCE) == {"client_secret": "s3cr3t"}
    assert "s3cr3t" not in repr(credential)


def test_a_certificate_signs_a_client_assertion() -> None:
    key, cert = certificate()
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credential = ClientCertificate(pem_key, cert.public_bytes(serialization.Encoding.PEM))

    form = credential.form("app-a", AUDIENCE)

    assert form["client_assertion_type"] == (
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    )
    header, claims, signature = form["client_assertion"].split(".")
    thumbprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()
    assert decoded(header) == {
        "alg": "PS256",
        "typ": "JWT",
        "x5t#S256": base64.urlsafe_b64encode(thumbprint).rstrip(b"=").decode(),
    }
    payload = decoded(claims)
    assert {key: payload[key] for key in ("aud", "iss", "sub")} == {
        "aud": AUDIENCE,
        "iss": "app-a",
        "sub": "app-a",
    }
    now = time.time()
    assert isinstance(payload["nbf"], int) and isinstance(payload["exp"], int)
    assert payload["iat"] == payload["nbf"] and abs(payload["nbf"] - now) < 60
    assert payload["exp"] - payload["nbf"] == 600
    key.public_key().verify(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)),
        f"{header}.{claims}".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    assert "PRIVATE" not in repr(credential)


def test_each_assertion_has_its_own_id() -> None:
    key, cert = certificate()
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credential = ClientCertificate(pem_key, cert.public_bytes(serialization.Encoding.PEM))

    first, second = (
        decoded(credential.form("app-a", AUDIENCE)["client_assertion"].split(".")[1])
        for _ in range(2)
    )

    assert first["jti"] != second["jti"]


def test_a_certificate_key_must_be_rsa() -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    _, cert = certificate()
    pem_key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    with pytest.raises(ValueError, match="RSA"):
        ClientCertificate(pem_key, cert.public_bytes(serialization.Encoding.PEM))
