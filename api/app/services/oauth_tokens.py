"""Minting and verifying Roundhouse MCP access tokens.

The product of every grant type is the same JWT (docs/mcp-auth-id-jag.md §7):

    iss = this Roundhouse instance (public base URL)
    aud = ONE server's canonical MCP URL (<base>/s/{name}/mcp)  [RFC 8707]
    sub = the human (or service identity) — powers per-user audit
    client_id, scope, iat/exp/jti

Scope issuance policy (§9 "scope-aware grants"): the caller gets
`allowed ∩ requested`, where allowed today is *all of the server's registered
scopes* for any user who can access the server (per-grant scope subsets are the
schema's third dimension, added later — default-all preserves current
behaviour). Requested-but-not-allowed scopes are silently dropped, per RFC 6749
§3.3 (the token's `scope` claim is the authoritative result).
"""
from __future__ import annotations

import secrets
import time
import uuid

from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ServerScope, User
from app.services import oauth_keys
from app.services.permissions import can_access

_jwt = JsonWebToken(["RS256"])


class OAuthTokenError(RuntimeError):
    """Verification failure. Callers map to invalid_grant / 401."""


def issuer() -> str:
    """The AS issuer identifier — the deploy-time public base URL, same
    derivation as sso_config.redirect_uri()."""
    return get_settings().mcp_base_url.rstrip("/")


def resource_url(server_name: str) -> str:
    """Canonical resource identifier for one spawned server (the token `aud`)."""
    return f"{issuer()}/s/{server_name}/mcp"


def server_name_from_resource(resource: str) -> str | None:
    """Parse the server name out of a resource indicator. Accepts the canonical
    `<base>/s/{name}/mcp` and the trailing-slash / no-suffix variants clients
    actually send. None when the URL isn't one of ours."""
    base = issuer()
    value = (resource or "").strip().rstrip("/")
    if not value.startswith(base + "/s/"):
        return None
    tail = value[len(base) + 3 :]
    name = tail.split("/", 1)[0]
    return name or None


def registered_scopes(db: Session, server_name: str) -> list[str]:
    rows = (
        db.query(ServerScope)
        .filter(ServerScope.server_name == server_name)
        .order_by(ServerScope.name)
        .all()
    )
    return [r.name for r in rows]


def allowed_scopes_for(db: Session, user: User, server_name: str) -> list[str] | None:
    """The scope set this user may be issued on this server, or None when the
    user may not access the server at all. Default-all (see module docstring)."""
    if not can_access(db, user, server_name):
        return None
    return registered_scopes(db, server_name)


def grant_scopes(allowed: list[str], requested: list[str] | None) -> list[str]:
    """allowed ∩ requested; an absent/empty request means 'everything allowed'."""
    if not requested:
        return list(allowed)
    req = set(requested)
    return [s for s in allowed if s in req]


def mint_access_token(
    db: Session,
    *,
    user_sub: str,
    client_id: str,
    server_name: str,
    scopes: list[str],
    ttl_seconds: int | None = None,
    extra_claims: dict | None = None,
) -> tuple[str, dict]:
    """Sign an access token. Returns (compact JWT, claims). `user_sub` is the
    stable subject we audit on — email for humans (matches request_events
    attribution ergonomics), client_id for pure machine identities."""
    now = int(time.time())
    ttl = ttl_seconds or get_settings().oauth_access_token_ttl_seconds
    claims = {
        "iss": issuer(),
        "aud": resource_url(server_name),
        "sub": user_sub,
        "client_id": client_id,
        "scope": " ".join(scopes),
        "iat": now,
        "exp": now + max(60, int(ttl)),
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        claims.update(extra_claims)
    key = oauth_keys.signing_key(db)
    header = {"alg": "RS256", "kid": oauth_keys.signing_kid(db), "typ": "JWT"}
    token = _jwt.encode(header, claims, key)
    return token.decode("ascii") if isinstance(token, bytes) else token, claims


def verify_access_token(
    db: Session, token: str, *, audience: str | None = None
) -> dict:
    """Validate one of our own access tokens (introspection endpoint + tests;
    spawned servers verify against the JWKS themselves and never call this)."""
    key_set = JsonWebKey.import_key_set(oauth_keys.public_jwks(db))
    claims_options = {
        "iss": {"essential": True, "value": issuer()},
        "exp": {"essential": True},
    }
    if audience:
        claims_options["aud"] = {"essential": True, "value": audience}
    try:
        claims = _jwt.decode(token, key_set, claims_options=claims_options)
        claims.validate()
    except (JoseError, ValueError) as e:
        raise OAuthTokenError(f"access token invalid: {e}") from e
    return dict(claims)


def new_opaque_token(prefix: str) -> str:
    """Opaque credential (auth code / refresh token): unguessable, prefixed so
    a leaked string is identifiable in logs scrubbing."""
    return prefix + secrets.token_urlsafe(48)
