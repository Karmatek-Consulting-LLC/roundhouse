"""OAuth client registry: manual, DCR (RFC 7591), and CIMD registration.

Trust model (docs/mcp-auth-id-jag.md §7, field guide §05):
  - `trusted` (manual/admin-created only) unlocks consent-skip and the
    jwt-bearer grant. DCR/CIMD clients are never trusted — DCR is anonymous
    and CIMD only proves control of a URL, not organizational blessing.
  - Public clients (auth method "none") have no secret; PKCE carries the
    binding instead. Confidential clients authenticate with a secret we store
    only as sha256 (same discipline as personal access tokens).

CIMD ("Client ID Metadata Documents"): the client_id IS an https URL that
serves the client's own metadata JSON. First sight of one: fetch, validate
(document client_id must equal its own URL), cache as an oauth_clients row so
later flows are offline. Refetched when stale.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import OAuthClient

_CIMD_TIMEOUT = 10.0
_CIMD_MAX_BYTES = 64 * 1024
_CIMD_REFRESH = timedelta(hours=24)

_ALLOWED_AUTH_METHODS = {"none", "client_secret_basic", "client_secret_post"}
_DEFAULT_GRANTS = ["authorization_code", "refresh_token"]


class ClientRegistrationError(ValueError):
    """Invalid registration request/metadata. Maps to invalid_client_metadata."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_redirect_uris(uris: list) -> list[str]:
    out: list[str] = []
    for u in uris or []:
        u = str(u).strip()
        if not u:
            continue
        # OAuth 2.1: https, or localhost loopback for native clients.
        if u.startswith("https://") or u.startswith("http://localhost") \
                or u.startswith("http://127.0.0.1"):
            out.append(u)
        else:
            raise ClientRegistrationError(f"redirect_uri not allowed: {u}")
    return out


# ---------- lookup / authentication ----------


def get_client(db: Session, client_id: str) -> OAuthClient | None:
    if not client_id:
        return None
    row = db.get(OAuthClient, client_id)
    if row is not None:
        return row
    if _is_cimd_id(client_id):
        return _resolve_cimd(db, client_id)
    return None


@dataclass
class ClientAuth:
    client: OAuthClient
    authenticated: bool  # secret verified (confidential) vs merely identified (public)


def authenticate_client(
    db: Session,
    *,
    client_id: str | None,
    client_secret: str | None,
) -> ClientAuth | None:
    """Resolve + authenticate a token-endpoint caller. None => unknown client
    or bad secret (callers answer 401 invalid_client). A public client with no
    secret comes back authenticated=False — enough for PKCE flows, never for
    jwt-bearer."""
    client = get_client(db, client_id or "")
    if client is None:
        return None
    if client.token_endpoint_auth_method == "none":
        if client_secret:
            return None  # a secret from a secretless client is a config smell
        return ClientAuth(client, authenticated=False)
    if not client_secret or not client.client_secret_hash:
        return None
    if not secrets.compare_digest(_sha256(client_secret), client.client_secret_hash):
        return None
    return ClientAuth(client, authenticated=True)


def redirect_uri_allowed(client: OAuthClient, redirect_uri: str) -> bool:
    """OAuth 2.1: exact string match against the registered list."""
    return redirect_uri in list(client.redirect_uris or [])


# ---------- manual registration (admin; the Valkyrie path) ----------


def create_manual_client(
    db: Session,
    *,
    client_name: str,
    trusted: bool = False,
    confidential: bool = True,
    redirect_uris: list[str] | None = None,
    grant_types: list[str] | None = None,
) -> tuple[OAuthClient, str | None]:
    """Admin-created client. Returns (row, plaintext secret or None) — the
    secret is shown once and stored only as a hash, like PAT issuance."""
    client_id = "rhc_" + uuid.uuid4().hex
    secret = None
    if confidential:
        secret = "rhs_" + secrets.token_urlsafe(40)
    row = OAuthClient(
        client_id=client_id,
        client_secret_hash=_sha256(secret) if secret else None,
        client_name=client_name.strip() or client_id,
        token_endpoint_auth_method="client_secret_basic" if confidential else "none",
        redirect_uris=_validate_redirect_uris(redirect_uris or []),
        grant_types=list(grant_types or _DEFAULT_GRANTS),
        registration_type="manual",
        trusted=trusted,
    )
    db.add(row)
    db.flush()
    return row, secret


# ---------- DCR (RFC 7591) ----------


def register_dcr_client(db: Session, metadata: dict) -> tuple[OAuthClient, str | None]:
    """Anonymous self-registration. We accept the metadata subset the MCP
    ecosystem actually sends and ignore the rest (per RFC 7591 §2, unknown
    fields MUST be ignored)."""
    redirect_uris = _validate_redirect_uris(metadata.get("redirect_uris") or [])
    if not redirect_uris:
        raise ClientRegistrationError("redirect_uris is required")
    auth_method = str(
        metadata.get("token_endpoint_auth_method") or "none"
    ).strip()
    if auth_method not in _ALLOWED_AUTH_METHODS:
        raise ClientRegistrationError(
            f"token_endpoint_auth_method not supported: {auth_method}"
        )
    grants = [
        g for g in (metadata.get("grant_types") or _DEFAULT_GRANTS)
        if g in ("authorization_code", "refresh_token")
    ] or _DEFAULT_GRANTS

    client_id = "rhc_" + uuid.uuid4().hex
    secret = None
    if auth_method != "none":
        secret = "rhs_" + secrets.token_urlsafe(40)
    row = OAuthClient(
        client_id=client_id,
        client_secret_hash=_sha256(secret) if secret else None,
        client_name=str(metadata.get("client_name") or "")[:255],
        token_endpoint_auth_method=auth_method,
        redirect_uris=redirect_uris,
        grant_types=grants,
        registration_type="dcr",
        trusted=False,
    )
    db.add(row)
    db.flush()
    return row, secret


# ---------- CIMD ----------


def _is_cimd_id(client_id: str) -> bool:
    return client_id.startswith("https://")


def _resolve_cimd(db: Session, url: str) -> OAuthClient | None:
    doc = _fetch_cimd_document(url)
    if doc is None:
        return None
    try:
        row = _row_from_cimd(url, doc)
    except ClientRegistrationError:
        return None
    db.add(row)
    db.flush()
    return row


def refresh_cimd_if_stale(db: Session, client: OAuthClient) -> OAuthClient:
    """CIMD rows are a cache of the vendor's document; refetch daily so a
    vendor rotating their redirect URIs doesn't strand users for long."""
    if client.registration_type != "cimd":
        return client
    created = client.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and datetime.now(timezone.utc) - created < _CIMD_REFRESH:
        return client
    doc = _fetch_cimd_document(client.client_id)
    if doc is None:
        return client  # keep serving the cache on transient fetch failure
    try:
        fresh = _row_from_cimd(client.client_id, doc)
    except ClientRegistrationError:
        return client
    client.client_name = fresh.client_name
    client.redirect_uris = fresh.redirect_uris
    client.grant_types = fresh.grant_types
    client.created_at = datetime.now(timezone.utc)
    return client


def _fetch_cimd_document(url: str) -> dict | None:
    try:
        with httpx.Client(timeout=_CIMD_TIMEOUT, follow_redirects=False) as http:
            resp = http.get(url, headers={"accept": "application/json"})
            if resp.status_code != 200 or len(resp.content) > _CIMD_MAX_BYTES:
                return None
            return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def _row_from_cimd(url: str, doc: dict) -> OAuthClient:
    if str(doc.get("client_id") or "").rstrip("/") != url.rstrip("/"):
        # The document must claim the URL it is served from — that equality is
        # the entire proof of control.
        raise ClientRegistrationError("CIMD document client_id != document URL")
    redirect_uris = _validate_redirect_uris(doc.get("redirect_uris") or [])
    if not redirect_uris:
        raise ClientRegistrationError("CIMD document has no redirect_uris")
    return OAuthClient(
        client_id=url,
        client_secret_hash=None,
        client_name=str(doc.get("client_name") or "")[:255],
        token_endpoint_auth_method="none",
        redirect_uris=redirect_uris,
        grant_types=["authorization_code", "refresh_token"],
        registration_type="cimd",
        trusted=False,
    )
