"""OIDC client: PKCE helpers + ID-token validation against a JWKS."""
from __future__ import annotations

import base64
import hashlib
import time

import pytest
from authlib.jose import JsonWebKey, JsonWebToken

from app.services.oidc import OidcClient, OidcError

ISSUER = "https://login.microsoftonline.com/tenant-1/v2.0"
CLIENT_ID = "client-abc"


def _client() -> OidcClient:
    return OidcClient(
        discovery_url="https://example/.well-known/openid-configuration",
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="secret",
        redirect_uri="https://app/api/auth/oidc/callback",
    )


@pytest.fixture
def signing():
    """An RSA key plus an OidcClient wired to validate against its public JWKS."""
    key = JsonWebKey.generate_key("RSA", 2048, {"kid": "k1"}, is_private=True)
    public = key.as_dict(is_private=False)
    client = _client()
    client._jwks_keys = lambda force=False: JsonWebKey.import_key_set({"keys": [public]})
    return key, client


def _make_token(key, payload: dict) -> str:
    jwt = JsonWebToken(["RS256"])
    return jwt.encode({"alg": "RS256", "kid": "k1"}, payload, key).decode("ascii")


def _base_claims(**over) -> dict:
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-1",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
        "nonce": "nonce-1",
        "email": "u@corp.com",
        "name": "U",
    }
    claims.update(over)
    return claims


# ---------- PKCE ----------

def test_pkce_challenge_matches_spec():
    verifier = "test-verifier-value-1234567890"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert OidcClient.pkce_challenge(verifier) == expected


def test_pkce_verifier_length_in_range():
    v = OidcClient.new_pkce_verifier()
    assert 43 <= len(v) <= 128


# ---------- ID token validation ----------

def test_valid_token_returns_claims(signing):
    key, client = signing
    token = _make_token(key, _base_claims())
    claims = client.validate_id_token(token, nonce="nonce-1")
    assert claims["sub"] == "user-1"
    assert claims["email"] == "u@corp.com"


def test_nonce_mismatch_rejected(signing):
    key, client = signing
    token = _make_token(key, _base_claims(nonce="attacker"))
    with pytest.raises(OidcError):
        client.validate_id_token(token, nonce="nonce-1")


def test_expired_token_rejected(signing):
    key, client = signing
    token = _make_token(key, _base_claims(exp=int(time.time()) - 10))
    with pytest.raises(OidcError):
        client.validate_id_token(token, nonce="nonce-1")


def test_wrong_audience_rejected(signing):
    key, client = signing
    token = _make_token(key, _base_claims(aud="some-other-app"))
    with pytest.raises(OidcError):
        client.validate_id_token(token, nonce="nonce-1")


def test_wrong_issuer_rejected(signing):
    key, client = signing
    token = _make_token(key, _base_claims(iss="https://evil/issuer"))
    with pytest.raises(OidcError):
        client.validate_id_token(token, nonce="nonce-1")


def test_signature_from_unknown_key_rejected(signing):
    _key, client = signing
    other = JsonWebKey.generate_key("RSA", 2048, {"kid": "k2"}, is_private=True)
    token = _make_token(other, _base_claims())
    with pytest.raises(OidcError):
        client.validate_id_token(token, nonce="nonce-1")
