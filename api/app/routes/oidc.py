"""Entra ID SSO routes (Phase 1 — dashboard).

Authorization Code + PKCE, validated entirely server-side, ending in the same
personal-access-token the password flow issues. Everything downstream of login
(the SPA's AuthProvider session) is unchanged — only token issuance differs.

    GET /api/auth/oidc/status    public; {enabled} so the login page knows
                                 whether to render the Microsoft button.
    GET /api/auth/oidc/login     redirect to Entra; stashes the PKCE/state/nonce
                                 transaction in a short-lived signed cookie.
    GET /api/auth/oidc/callback  verify -> JIT upsert -> sync grants -> mint PAT
                                 -> redirect the SPA with the token in the URL
                                 fragment (never sent to a server / logged).

The transaction cookie is encrypted+MAC'd with the app's AES envelope (keyed off
APP_KEY), so the server keeps no per-login state — this stays correct across
multiple API workers.
"""
from __future__ import annotations

import json
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import issue_token
from app.config import Settings, get_settings
from app.crypto import DecryptError, decrypt, encrypt
from app.db import get_db
from app.services import oidc as oidc_service
from app.services.claim_mapping import resolve_grants
from app.services.oidc import OidcError
from app.services.sso import SsoError, sync_grants, upsert_sso_user

logger = logging.getLogger("roundhouse-api")

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])

# Name of the encrypted transaction cookie and how long a login may take from
# /login to /callback before we reject the round-trip as stale.
_TX_COOKIE = "rh_oidc_tx"
_TX_MAX_AGE_SECONDS = 600
_COOKIE_PATH = "/api/auth/oidc"


def _https(request: Request) -> bool:
    """Whether the *original* client request was HTTPS. Behind Traefik the
    in-cluster hop is plain HTTP, so trust X-Forwarded-Proto when present."""
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _login_error_redirect(message: str) -> RedirectResponse:
    """Send the browser back to the SPA login page with a surfaced error."""
    resp = RedirectResponse(url=f"/login?sso_error={quote(message)}", status_code=302)
    resp.delete_cookie(_TX_COOKIE, path=_COOKIE_PATH)
    return resp


@router.get("/status")
def oidc_status(settings: Settings = Depends(get_settings)) -> dict:
    return {"enabled": settings.oidc_enabled}


@router.get("/login")
def oidc_login(request: Request, settings: Settings = Depends(get_settings)):
    if not settings.oidc_enabled:
        return _login_error_redirect("SSO is not configured")
    if not settings.app_key:
        # We sign the transaction cookie with APP_KEY; refuse rather than fall
        # back to an unsigned (forgeable) state cookie.
        logger.error("OIDC login attempted but APP_KEY is not set")
        return _login_error_redirect("SSO is misconfigured (no APP_KEY)")

    client = oidc_service.get_client(settings)
    verifier = client.new_pkce_verifier()
    state = client.new_state()
    nonce = client.new_nonce()
    try:
        auth_url = client.build_authorization_url(
            state=state, nonce=nonce, code_challenge=client.pkce_challenge(verifier)
        )
    except OidcError as e:
        logger.error("Failed to build authorization URL: %s", e)
        return _login_error_redirect("Could not reach the identity provider")

    tx = encrypt(
        json.dumps({"state": state, "nonce": nonce, "verifier": verifier, "ts": int(time.time())}),
        settings.app_key,
    )
    resp = RedirectResponse(url=auth_url, status_code=302)
    resp.set_cookie(
        _TX_COOKIE,
        tx,
        max_age=_TX_MAX_AGE_SECONDS,
        httponly=True,
        secure=_https(request),
        samesite="lax",  # allows the cookie on Entra's top-level GET redirect back
        path=_COOKIE_PATH,
    )
    return resp


@router.get("/callback")
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not settings.oidc_enabled:
        return _login_error_redirect("SSO is not configured")

    params = request.query_params
    if params.get("error"):
        # Entra rejected the auth request (consent denied, etc.).
        desc = params.get("error_description") or params.get("error")
        return _login_error_redirect(desc)

    code = params.get("code")
    state = params.get("state")
    if not code or not state:
        return _login_error_redirect("Malformed SSO response")

    tx = _read_transaction(request, settings)
    if tx is None:
        return _login_error_redirect("SSO session expired; please try again")
    # State must match the value minted in /login (CSRF defense).
    import secrets as _secrets

    if not _secrets.compare_digest(str(tx.get("state", "")), state):
        return _login_error_redirect("SSO state mismatch; please try again")

    client = oidc_service.get_client(settings)
    try:
        tokens = client.exchange_code(code=code, code_verifier=tx["verifier"])
        id_token = tokens.get("id_token")
        if not id_token:
            raise OidcError("token response had no id_token")
        claims = client.validate_id_token(id_token, nonce=tx["nonce"])
    except OidcError as e:
        logger.warning("OIDC callback validation failed: %s", e)
        return _login_error_redirect("Sign-in could not be verified")

    try:
        user = upsert_sso_user(db, claims)
        sync_grants(db, user, resolve_grants(db, claims))
        db.flush()
        token = issue_token(db, user, name="sso")
    except SsoError as e:
        logger.warning("SSO provisioning failed: %s", e)
        return _login_error_redirect(str(e))

    # Token rides back in the URL fragment: it is never sent to a server and
    # stays out of access logs / Referer headers. The SPA callback reads it.
    target = f"{settings.oidc_post_login_redirect}#token={quote(token)}"
    resp = RedirectResponse(url=target, status_code=302)
    resp.delete_cookie(_TX_COOKIE, path=_COOKIE_PATH)
    return resp


def _read_transaction(request: Request, settings: Settings) -> dict | None:
    """Decrypt + validate the transaction cookie. None if absent, tampered, or
    older than the allowed round-trip window."""
    raw = request.cookies.get(_TX_COOKIE)
    if not raw or not settings.app_key:
        return None
    try:
        payload = json.loads(decrypt(raw, settings.app_key))
    except (DecryptError, ValueError):
        return None
    ts = payload.get("ts")
    if not isinstance(ts, int) or (int(time.time()) - ts) > _TX_MAX_AGE_SECONDS:
        return None
    if not all(payload.get(k) for k in ("state", "nonce", "verifier")):
        return None
    return payload
