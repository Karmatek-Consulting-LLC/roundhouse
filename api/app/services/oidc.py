"""OIDC client — discovery, JWKS cache, auth-code/PKCE helpers, token validation.

Deliberately self-contained and provider-agnostic so Phase 2 (MCP server auth)
can reuse it verbatim: the only Entra-specific knowledge lives in the issuer /
discovery URL built in app.config. Everything here speaks plain OIDC.

The flow this supports (Authorization Code + PKCE, validated server-side):

    build_authorization_url(...) -> redirect the browser to the IdP
    exchange_code(code, verifier) -> token endpoint, returns id_token/access_token
    validate_id_token(id_token, nonce) -> verified claims dict

Discovery metadata and the JWKS are cached in-process with a short TTL; key
rotation is picked up on the next refresh (and a kid miss forces an immediate
refetch, so a freshly-rotated key still validates).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

import httpx
from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from app.config import Settings

# Discovery + JWKS cache TTL. Short enough to follow rotation, long enough that
# a burst of logins doesn't hammer the IdP.
_CACHE_TTL_SECONDS = 3600
# PKCE: RFC 7636 allows 43-128 chars in the verifier. token_urlsafe(64) -> ~86.
_VERIFIER_BYTES = 64


class OidcError(RuntimeError):
    """Any failure in the OIDC exchange or validation. Carries an operator-safe
    message; callers map it to a 401/502 as appropriate."""


@dataclass
class _Cached:
    value: dict
    fetched_at: float

    def fresh(self, now: float) -> bool:
        return (now - self.fetched_at) < _CACHE_TTL_SECONDS


class OidcClient:
    """One client per (issuer, client_id). Construct from Settings.

    Holds its own small caches; safe to construct per-request (cheap) but the
    caches are instance-level, so prefer the module-level `get_client()` which
    returns a process-wide singleton keyed off the live settings."""

    def __init__(
        self,
        *,
        discovery_url: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http_timeout: float = 10.0,
    ) -> None:
        self.discovery_url = discovery_url
        self.issuer = issuer
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.http_timeout = http_timeout
        self._discovery: _Cached | None = None
        self._jwks: _Cached | None = None
        self._jwt = JsonWebToken(["RS256"])

    # ---------- construction ----------

    @classmethod
    def from_settings(cls, settings: Settings) -> "OidcClient":
        return cls(
            discovery_url=settings.entra_discovery_url,
            issuer=settings.entra_issuer,
            client_id=settings.entra_client_id,
            client_secret=settings.entra_client_secret,
            redirect_uri=settings.entra_redirect_uri,
        )

    # ---------- discovery / jwks ----------

    def _now(self) -> float:
        return time.monotonic()

    def _discovery_doc(self) -> dict:
        now = self._now()
        if self._discovery and self._discovery.fresh(now):
            return self._discovery.value
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                resp = client.get(self.discovery_url)
                resp.raise_for_status()
                doc = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"OIDC discovery failed: {e}") from e
        self._discovery = _Cached(doc, now)
        return doc

    def _jwks_keys(self, *, force: bool = False) -> JsonWebKey:
        now = self._now()
        if not force and self._jwks and self._jwks.fresh(now):
            return JsonWebKey.import_key_set(self._jwks.value)
        jwks_uri = self._discovery_doc().get("jwks_uri")
        if not jwks_uri:
            raise OidcError("OIDC discovery document has no jwks_uri")
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                resp = client.get(jwks_uri)
                resp.raise_for_status()
                raw = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise OidcError(f"Fetching JWKS failed: {e}") from e
        self._jwks = _Cached(raw, now)
        return JsonWebKey.import_key_set(raw)

    # ---------- PKCE ----------

    @staticmethod
    def new_pkce_verifier() -> str:
        return secrets.token_urlsafe(_VERIFIER_BYTES)

    @staticmethod
    def pkce_challenge(verifier: str) -> str:
        import base64
        import hashlib

        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    @staticmethod
    def new_state() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def new_nonce() -> str:
        return secrets.token_urlsafe(32)

    # ---------- flow ----------

    def build_authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, scope: str = "openid profile email"
    ) -> str:
        from urllib.parse import urlencode

        endpoint = self._discovery_doc().get("authorization_endpoint")
        if not endpoint:
            raise OidcError("OIDC discovery document has no authorization_endpoint")
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{endpoint}?{urlencode(params)}"

    def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        """Trade the auth code for tokens. Returns the raw token-endpoint JSON
        (id_token, access_token, ...)."""
        endpoint = self._discovery_doc().get("token_endpoint")
        if not endpoint:
            raise OidcError("OIDC discovery document has no token_endpoint")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        }
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                resp = client.post(endpoint, data=data)
        except httpx.HTTPError as e:
            raise OidcError(f"Token exchange request failed: {e}") from e
        if resp.status_code != 200:
            # Surface the IdP's error code but never the full body (may echo the
            # client secret back in some error shapes).
            detail = ""
            try:
                detail = resp.json().get("error", "")
            except ValueError:
                pass
            raise OidcError(f"Token exchange rejected by IdP ({resp.status_code} {detail})")
        try:
            return resp.json()
        except ValueError as e:
            raise OidcError(f"Token endpoint returned non-JSON: {e}") from e

    def validate_id_token(self, id_token: str, *, nonce: str) -> dict:
        """Verify signature (JWKS), iss, aud, exp, and nonce. Returns claims.

        Raises OidcError on any validation failure — callers must treat that as
        an authentication failure, never a partial success."""
        claims_options = {
            "iss": {"essential": True, "value": self.issuer},
            "aud": {"essential": True, "value": self.client_id},
            "exp": {"essential": True},
        }
        claims = self._decode_with_rotation(id_token, claims_options)
        try:
            claims.validate()  # exp/iat/nbf + the essential/value checks above
        except JoseError as e:
            raise OidcError(f"ID token claims invalid: {e}") from e

        token_nonce = claims.get("nonce")
        if not token_nonce or not secrets.compare_digest(str(token_nonce), nonce):
            raise OidcError("ID token nonce mismatch")
        return dict(claims)

    def _decode_with_rotation(self, id_token: str, claims_options: dict):
        """Decode against the cached JWKS; on a key-id miss, force one refresh
        and retry so a freshly-rotated signing key validates immediately."""
        # A kid that isn't in the cached set raises ValueError (not JoseError),
        # so catch both: refresh once, then give up.
        try:
            return self._jwt.decode(id_token, self._jwks_keys(), claims_options=claims_options)
        except (JoseError, ValueError):
            try:
                return self._jwt.decode(
                    id_token, self._jwks_keys(force=True), claims_options=claims_options
                )
            except (JoseError, ValueError) as e:
                raise OidcError(f"ID token signature/decoding invalid: {e}") from e


# ---------- process-wide singleton ----------

_client: OidcClient | None = None
_client_key: tuple | None = None


def get_client(settings: Settings) -> OidcClient:
    """Return a cached client, rebuilding it if the relevant settings changed
    (so a config reload / test monkeypatch is honoured without a restart)."""
    global _client, _client_key
    key = (
        settings.entra_discovery_url,
        settings.entra_client_id,
        settings.entra_client_secret,
        settings.entra_redirect_uri,
    )
    if _client is None or _client_key != key:
        _client = OidcClient.from_settings(settings)
        _client_key = key
    return _client


def reset_client_for_tests() -> None:
    global _client, _client_key
    _client = None
    _client_key = None
