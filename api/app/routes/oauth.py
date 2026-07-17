"""The Roundhouse authorization server's front door: one token desk, three
grant types (docs/mcp-auth-id-jag.md; field guide §01–§07).

    GET/POST /oauth/authorize   interactive flow: PKCE + AS session + consent
    POST     /oauth/consent     consent form submit
    GET      /oauth/entra/start,/oauth/entra/callback   federated login leg
    POST     /oauth/token       authorization_code | refresh_token | jwt-bearer
    POST     /oauth/register    DCR (RFC 7591), policy-gated
    POST     /oauth/introspect  RFC 7662 (client-authenticated; for the lab/tests)
    POST     /oauth/revoke      RFC 7009 (refresh tokens)

Sessions: the AS session is an encrypted cookie (app.crypto envelope, like the
OIDC transaction cookie) scoped to /oauth — this is what makes connecting a
second MCP server a silent redirect instead of a second login. In-flight
authorize requests round-trip through login/consent forms as an encrypted
"request blob" so the server stays stateless across workers.
"""
from __future__ import annotations

import html
import json
import logging
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import logbook
from app.auth import verify_password
from app.config import get_settings
from app.crypto import DecryptError, decrypt, encrypt
from app.db import get_db
from app.logbook import CONTEXT_AUTH
from app.models import OAuthConsent, User
from app.platform_settings import SETTING_OAUTH_DCR_ENABLED, get_setting
from app.services import oauth_assertions, oauth_clients, oauth_flows, oauth_tokens
from app.services import sso_config
from app.services.claim_mapping import AccessDenied, resolve_grants
from app.services.oauth_assertions import AssertionError_
from app.services.oauth_clients import ClientRegistrationError
from app.services.oauth_flows import FlowError
from app.services.oidc import OidcClient, OidcError
from app.services.sso import SsoError, sync_grants, upsert_sso_user

logger = logging.getLogger("roundhouse-api")

router = APIRouter(tags=["oauth"])

_SESSION_COOKIE = "rh_as_session"
_TX_COOKIE = "rh_oauth_astx"
_COOKIE_PATH = "/oauth"
_BLOB_MAX_AGE_SECONDS = 600
_JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"


def _https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


# ---------- AS session cookie ----------


def _session_user(request: Request, db: Session) -> User | None:
    raw = request.cookies.get(_SESSION_COOKIE)
    app_key = get_settings().app_key
    if not raw or not app_key:
        return None
    try:
        payload = json.loads(decrypt(raw, app_key))
    except (DecryptError, ValueError):
        return None
    ts = payload.get("ts")
    ttl = get_settings().oauth_session_ttl_hours * 3600
    if not isinstance(ts, int) or (int(time.time()) - ts) > ttl:
        return None
    uid = payload.get("uid")
    return db.get(User, uid) if uid else None


def _set_session(resp, request: Request, user: User) -> None:
    app_key = get_settings().app_key
    if not app_key:
        return
    value = encrypt(json.dumps({"uid": user.id, "ts": int(time.time())}), app_key)
    resp.set_cookie(
        _SESSION_COOKIE,
        value,
        max_age=get_settings().oauth_session_ttl_hours * 3600,
        httponly=True,
        secure=_https(request),
        samesite="lax",
        path=_COOKIE_PATH,
    )


# ---------- request blob (in-flight authorize params through forms) ----------


def _pack_request(params: dict) -> str:
    return encrypt(
        json.dumps({"p": params, "ts": int(time.time())}), get_settings().app_key
    )


def _unpack_request(blob: str) -> dict | None:
    try:
        payload = json.loads(decrypt(blob, get_settings().app_key))
    except (DecryptError, ValueError):
        return None
    ts = payload.get("ts")
    if not isinstance(ts, int) or (int(time.time()) - ts) > _BLOB_MAX_AGE_SECONDS:
        return None
    p = payload.get("p")
    return p if isinstance(p, dict) else None


# ---------- tiny HTML (login / consent / error) ----------

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Roundhouse</title>
<style>
 body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:#f4f6f7;color:#1c2428;display:grid;place-items:center;
      min-height:100vh;margin:0}}
 .card{{background:#fff;border:1px solid #dde3e6;border-radius:10px;
       padding:2rem;max-width:24rem;width:calc(100% - 2rem);
       box-shadow:0 1px 3px rgba(20,30,35,.06)}}
 h1{{font-size:1.15rem;margin:0 0 .35rem}} p{{margin:.4rem 0;color:#51626b}}
 label{{display:block;font-size:.85rem;margin:.8rem 0 .2rem;color:#51626b}}
 input[type=email],input[type=password]{{width:100%;box-sizing:border-box;
   padding:.5rem .6rem;border:1px solid #c4ced3;border-radius:6px;font:inherit}}
 button{{margin-top:1rem;width:100%;padding:.55rem;border:0;border-radius:6px;
   background:#0e7490;color:#fff;font:inherit;font-weight:600;cursor:pointer}}
 button.secondary{{background:#e6ebee;color:#1c2428}}
 .brand{{font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;
   color:#0b5d74;font-weight:700;margin-bottom:1rem}}
 ul{{padding-left:1.2rem;color:#51626b}} code{{background:#eef1f3;padding:.1em .3em;
   border-radius:4px;font-size:.85em}}
 a.ms{{display:block;text-align:center;margin-top:.8rem;font-size:.9rem}}
</style></head><body><div class="card"><div class="brand">Roundhouse</div>
{body}</div></body></html>"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body),
                        status_code=status_code)


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return _page(
        "Sign-in error",
        f"<h1>Can't continue</h1><p>{html.escape(message)}</p>",
        status_code=status_code,
    )


def _login_page(blob: str, sso_enabled: bool, error: str | None = None) -> HTMLResponse:
    err = f'<p style="color:#b23b3b">{html.escape(error)}</p>' if error else ""
    ms = (
        f'<a class="ms" href="/oauth/entra/start?request={blob}">'
        "Continue with Microsoft</a>"
        if sso_enabled
        else ""
    )
    return _page(
        "Sign in",
        f"""<h1>Sign in to authorize</h1>
<p>An application is requesting access to an MCP server on your behalf.</p>{err}
<form method="post" action="/oauth/authorize">
<input type="hidden" name="request" value="{blob}">
<label>Email</label><input type="email" name="email" autocomplete="username" required>
<label>Password</label><input type="password" name="password"
 autocomplete="current-password" required>
<button type="submit">Sign in</button></form>{ms}""",
    )


def _consent_page(blob: str, client_name: str, server: str,
                  scopes: list[str]) -> HTMLResponse:
    scope_list = (
        "<ul>" + "".join(f"<li><code>{html.escape(s)}</code></li>" for s in scopes)
        + "</ul>"
        if scopes
        else "<p>No named scopes — access follows the server's defaults.</p>"
    )
    return _page(
        "Authorize access",
        f"""<h1>Authorize {html.escape(client_name or "this application")}?</h1>
<p>It wants to call the MCP server <code>{html.escape(server)}</code> as you,
with these scopes:</p>{scope_list}
<p>This choice is remembered for this application.</p>
<form method="post" action="/oauth/consent">
<input type="hidden" name="request" value="{blob}">
<button type="submit" name="action" value="approve">Allow</button>
<button type="submit" name="action" value="deny" class="secondary">Deny</button>
</form>""",
    )


# ---------- /oauth/authorize ----------


def _redirect_err(redirect_uri: str, state: str | None, error: str,
                  description: str) -> RedirectResponse:
    q = {"error": error, "error_description": description}
    if state:
        q["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{sep}{urlencode(q)}", status_code=302)


def _authorize_params(request: Request) -> dict:
    keys = (
        "response_type", "client_id", "redirect_uri", "scope", "state",
        "code_challenge", "code_challenge_method", "resource",
    )
    return {k: request.query_params.get(k, "") for k in keys}


@router.get("/oauth/authorize")
def authorize(request: Request, db: Session = Depends(get_db)):
    return _authorize_continue(request, db, _authorize_params(request))


def _authorize_continue(request: Request, db: Session, p: dict):
    if not get_settings().app_key:
        return _error_page("The authorization server is not configured (no APP_KEY).",
                           status_code=500)

    client = oauth_clients.get_client(db, p.get("client_id") or "")
    if client is None:
        # Never redirect on an unvalidated client — render, don't bounce.
        return _error_page("Unknown client_id. Register the client first "
                           "(DCR at /oauth/register, or ask an admin).")
    oauth_clients.refresh_cimd_if_stale(db, client)

    redirect_uri = p.get("redirect_uri") or ""
    if not oauth_clients.redirect_uri_allowed(client, redirect_uri):
        return _error_page("redirect_uri is not registered for this client — "
                           "refusing to redirect anywhere else.")

    state = p.get("state") or ""
    if (p.get("response_type") or "") != "code":
        return _redirect_err(redirect_uri, state, "unsupported_response_type",
                             "only response_type=code is supported (OAuth 2.1)")
    if not p.get("code_challenge"):
        return _redirect_err(redirect_uri, state, "invalid_request",
                             "PKCE is required: send code_challenge (S256)")
    if (p.get("code_challenge_method") or "S256") != "S256":
        return _redirect_err(redirect_uri, state, "invalid_request",
                             "code_challenge_method must be S256")

    server = oauth_tokens.server_name_from_resource(p.get("resource") or "")
    if not server:
        return _redirect_err(
            redirect_uri, state, "invalid_target",
            "resource must name one MCP server on this platform, e.g. "
            f"{oauth_tokens.issuer()}/s/<name>/mcp (RFC 8707)",
        )

    user = _session_user(request, db)
    if user is None:
        blob = _pack_request(p)
        return _login_page(blob, sso_config.load(db).enabled)

    allowed = oauth_tokens.allowed_scopes_for(db, user, server)
    if allowed is None:
        logbook.record(CONTEXT_AUTH, "oauth.authorize", logbook.OUTCOME_DENIED,
                       request=request, user=user,
                       message=f"No access to server {server}",
                       detail={"client_id": client.client_id, "server": server})
        return _redirect_err(redirect_uri, state, "access_denied",
                             "you do not have access to this MCP server")
    requested = [s for s in (p.get("scope") or "").split() if s]
    scopes = oauth_tokens.grant_scopes(allowed, requested)

    needs_consent = not client.trusted and (
        db.query(OAuthConsent)
        .filter(OAuthConsent.user_id == user.id,
                OAuthConsent.client_id == client.client_id)
        .first()
        is None
    )
    if needs_consent:
        blob = _pack_request(p)
        return _consent_page(blob, client.client_name, server, scopes)

    return _issue_code_redirect(request, db, user, client, p, server, scopes)


def _issue_code_redirect(request: Request, db: Session, user: User, client,
                         p: dict, server: str, scopes: list[str]):
    code = oauth_flows.create_code(
        db,
        client_id=client.client_id,
        user_id=user.id,
        resource=oauth_tokens.resource_url(server),
        scopes=scopes,
        code_challenge=p["code_challenge"],
        code_challenge_method=p.get("code_challenge_method") or "S256",
        redirect_uri=p["redirect_uri"],
    )
    logbook.record(CONTEXT_AUTH, "oauth.authorize", logbook.OUTCOME_SUCCESS,
                   request=request, user=user,
                   message=f"Authorization code issued for {server}",
                   detail={"client_id": client.client_id, "server": server,
                           "scopes": scopes})
    q = {"code": code}
    if p.get("state"):
        q["state"] = p["state"]
    sep = "&" if "?" in p["redirect_uri"] else "?"
    resp = RedirectResponse(url=f"{p['redirect_uri']}{sep}{urlencode(q)}",
                            status_code=302)
    _set_session(resp, request, user)  # sliding session
    return resp


@router.post("/oauth/authorize")
def authorize_login(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
    request_blob: str = Form(alias="request"),
):
    p = _unpack_request(request_blob)
    if p is None:
        return _error_page("This sign-in link expired. Retry from your client.")
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None or not user.password_hash or not verify_password(
        password, user.password_hash
    ):
        logbook.record(CONTEXT_AUTH, "oauth.login", logbook.OUTCOME_FAILURE,
                       request=request, email=email,
                       message="OAuth login failed")
        return _login_page(_pack_request(p), sso_config.load(db).enabled,
                           error="Wrong email or password.")
    logbook.record(CONTEXT_AUTH, "oauth.login", logbook.OUTCOME_SUCCESS,
                   request=request, user=user, message="OAuth login")
    resp = _authorize_continue_with_session(request, db, p, user)
    _set_session(resp, request, user)
    return resp


def _authorize_continue_with_session(request: Request, db: Session, p: dict,
                                     user: User):
    """Re-run the authorize pipeline with an explicit user (fresh login —
    the session cookie isn't on this request yet)."""
    client = oauth_clients.get_client(db, p.get("client_id") or "")
    if client is None:
        return _error_page("Unknown client_id.")
    redirect_uri = p.get("redirect_uri") or ""
    if not oauth_clients.redirect_uri_allowed(client, redirect_uri):
        return _error_page("redirect_uri is not registered for this client.")
    server = oauth_tokens.server_name_from_resource(p.get("resource") or "")
    if not server:
        return _redirect_err(redirect_uri, p.get("state") or "", "invalid_target",
                             "resource must name one MCP server")
    allowed = oauth_tokens.allowed_scopes_for(db, user, server)
    if allowed is None:
        return _redirect_err(redirect_uri, p.get("state") or "", "access_denied",
                             "you do not have access to this MCP server")
    requested = [s for s in (p.get("scope") or "").split() if s]
    scopes = oauth_tokens.grant_scopes(allowed, requested)
    needs_consent = not client.trusted and (
        db.query(OAuthConsent)
        .filter(OAuthConsent.user_id == user.id,
                OAuthConsent.client_id == client.client_id)
        .first()
        is None
    )
    if needs_consent:
        return _consent_page(_pack_request(p), client.client_name, server, scopes)
    return _issue_code_redirect(request, db, user, client, p, server, scopes)


@router.post("/oauth/consent")
def consent_submit(
    request: Request,
    db: Session = Depends(get_db),
    action: str = Form(...),
    request_blob: str = Form(alias="request"),
):
    p = _unpack_request(request_blob)
    if p is None:
        return _error_page("This consent link expired. Retry from your client.")
    user = _session_user(request, db)
    if user is None:
        return _login_page(_pack_request(p), sso_config.load(db).enabled)
    client = oauth_clients.get_client(db, p.get("client_id") or "")
    if client is None:
        return _error_page("Unknown client_id.")
    redirect_uri = p.get("redirect_uri") or ""
    if not oauth_clients.redirect_uri_allowed(client, redirect_uri):
        return _error_page("redirect_uri is not registered for this client.")
    if action != "approve":
        return _redirect_err(redirect_uri, p.get("state") or "",
                             "access_denied", "the user denied the request")
    db.add(OAuthConsent(user_id=user.id, client_id=client.client_id))
    db.flush()
    return _authorize_continue_with_session(request, db, p, user)


# ---------- Entra federation leg for /oauth/authorize ----------


def _entra_oidc_client(cfg) -> OidcClient:
    # Same tenant + validation code as dashboard SSO, but our own callback so
    # the browser lands back in the authorize flow, not the SPA.
    return OidcClient(
        discovery_url=cfg.discovery_url,
        issuer=cfg.issuer,
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        redirect_uri=f"{oauth_tokens.issuer()}/oauth/entra/callback",
    )


@router.get("/oauth/entra/start")
def entra_start(request: Request, db: Session = Depends(get_db),
                request_blob: str = Query(alias="request")):
    cfg = sso_config.load(db)
    if not cfg.enabled:
        return _error_page("Microsoft sign-in is not configured.")
    p = _unpack_request(request_blob)
    if p is None:
        return _error_page("This sign-in link expired. Retry from your client.")
    client = _entra_oidc_client(cfg)
    verifier = client.new_pkce_verifier()
    state = client.new_state()
    nonce = client.new_nonce()
    try:
        url = client.build_authorization_url(
            state=state, nonce=nonce, code_challenge=client.pkce_challenge(verifier)
        )
    except OidcError as e:
        return _error_page(f"Could not reach the identity provider: {e}", 502)
    tx = encrypt(
        json.dumps({"state": state, "nonce": nonce, "verifier": verifier,
                    "p": p, "ts": int(time.time())}),
        get_settings().app_key,
    )
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(_TX_COOKIE, tx, max_age=_BLOB_MAX_AGE_SECONDS, httponly=True,
                    secure=_https(request), samesite="lax", path=_COOKIE_PATH)
    return resp


@router.get("/oauth/entra/callback")
def entra_callback(request: Request, db: Session = Depends(get_db)):
    cfg = sso_config.load(db)
    if not cfg.enabled:
        return _error_page("Microsoft sign-in is not configured.")
    raw = request.cookies.get(_TX_COOKIE)
    if not raw:
        return _error_page("Sign-in session expired; retry from your client.")
    try:
        tx = json.loads(decrypt(raw, get_settings().app_key))
    except (DecryptError, ValueError):
        return _error_page("Sign-in session invalid; retry from your client.")
    if (int(time.time()) - int(tx.get("ts") or 0)) > _BLOB_MAX_AGE_SECONDS:
        return _error_page("Sign-in session expired; retry from your client.")

    params = request.query_params
    if params.get("error"):
        return _error_page(params.get("error_description")
                           or params.get("error") or "Sign-in was rejected.")
    code, state = params.get("code"), params.get("state")
    import secrets as _secrets

    if not code or not state or not _secrets.compare_digest(
        str(tx.get("state", "")), state
    ):
        return _error_page("Sign-in response was malformed; retry from your client.")

    client = _entra_oidc_client(cfg)
    try:
        tokens = client.exchange_code(code=code, code_verifier=tx["verifier"])
        id_token = tokens.get("id_token")
        if not id_token:
            raise OidcError("token response had no id_token")
        claims = client.validate_id_token(id_token, nonce=tx["nonce"])
    except OidcError as e:
        logger.warning("OAuth Entra leg validation failed: %s", e)
        return _error_page("Sign-in could not be verified.")

    try:
        grants = resolve_grants(db, claims)
    except AccessDenied:
        return _error_page("Access denied: your account has not been granted a "
                           "role in Roundhouse. Contact your administrator.", 403)
    try:
        user = upsert_sso_user(db, claims,
                               link_local_by_email=sso_config.link_local_enabled(db))
        sync_grants(db, user, grants)
        db.flush()
    except SsoError as e:
        return _error_page(str(e))

    p = tx.get("p") if isinstance(tx.get("p"), dict) else None
    if p is None:
        return _error_page("Original request lost; retry from your client.")
    resp = _authorize_continue_with_session(request, db, p, user)
    _set_session(resp, request, user)
    resp.delete_cookie(_TX_COOKIE, path=_COOKIE_PATH)
    return resp


# ---------- /oauth/token ----------


def _token_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return JSONResponse({"error": error, "error_description": description},
                        status_code=status_code, headers=headers)


async def _client_credentials(request: Request, form: dict) -> tuple[str | None, str | None]:
    """client_secret_basic (Authorization header) or client_secret_post (form)."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("basic "):
        import base64

        try:
            raw = base64.b64decode(auth[6:]).decode("utf-8")
            cid, _, secret = raw.partition(":")
            from urllib.parse import unquote

            return unquote(cid), unquote(secret) if secret else None
        except Exception:  # noqa: BLE001
            return None, None
    return form.get("client_id") or None, form.get("client_secret") or None


@router.post("/oauth/token")
async def token(request: Request, db: Session = Depends(get_db)):
    form = dict((await request.form()).items())
    grant_type = form.get("grant_type") or ""

    client_id, client_secret = await _client_credentials(request, form)
    auth = oauth_clients.authenticate_client(
        db, client_id=client_id, client_secret=client_secret
    )
    if auth is None:
        return _token_error("invalid_client", "unknown client or bad credentials", 401)
    client = auth.client

    if grant_type == "authorization_code":
        return _grant_authorization_code(request, db, client, form)
    if grant_type == "refresh_token":
        return _grant_refresh(request, db, client, form)
    if grant_type == _JWT_BEARER:
        if not (client.trusted and auth.authenticated):
            # The privileged grant: acting for a user with no user present is
            # only for admin-blessed confidential clients (design §7).
            return _token_error(
                "unauthorized_client",
                "jwt-bearer is limited to trusted, confidential clients",
            )
        return _grant_jwt_bearer(request, db, client, form)
    return _token_error("unsupported_grant_type",
                        f"grant_type {grant_type!r} is not supported")


def _token_response(db: Session, *, user: User, client, server: str,
                    scopes: list[str], include_refresh: bool) -> JSONResponse:
    access, claims = oauth_tokens.mint_access_token(
        db,
        user_sub=user.email,
        client_id=client.client_id,
        server_name=server,
        scopes=scopes,
    )
    body = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": claims["exp"] - claims["iat"],
        "scope": " ".join(scopes),
    }
    if include_refresh:
        refresh = oauth_flows.issue_refresh(
            db,
            client_id=client.client_id,
            user_id=user.id,
            resource=oauth_tokens.resource_url(server),
            scopes=scopes,
        )
        body["refresh_token"] = refresh.token
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def _grant_authorization_code(request: Request, db: Session, client, form: dict):
    try:
        row = oauth_flows.redeem_code(
            db,
            code=form.get("code") or "",
            client_id=client.client_id,
            redirect_uri=form.get("redirect_uri") or "",
            code_verifier=form.get("code_verifier") or "",
        )
    except FlowError as e:
        return _token_error("invalid_grant", str(e))
    user = db.get(User, row.user_id)
    server = oauth_tokens.server_name_from_resource(row.resource)
    if user is None or server is None:
        return _token_error("invalid_grant", "grant no longer valid")
    # Re-check access at redemption so a grant cut between authorize and token
    # (or since the refresh was minted) is honoured immediately.
    allowed = oauth_tokens.allowed_scopes_for(db, user, server)
    if allowed is None:
        return _token_error("invalid_grant", "access to this server was revoked")
    scopes = oauth_tokens.grant_scopes(allowed, list(row.scopes or []) or None)
    logbook.record(CONTEXT_AUTH, "oauth.token", logbook.OUTCOME_SUCCESS,
                   request=request, user=user,
                   message=f"Access token issued for {server} (authorization_code)",
                   detail={"client_id": client.client_id, "server": server,
                           "grant": "authorization_code", "scopes": scopes})
    return _token_response(db, user=user, client=client, server=server,
                           scopes=scopes, include_refresh=True)


def _grant_refresh(request: Request, db: Session, client, form: dict):
    try:
        fresh = oauth_flows.rotate_refresh(
            db, token=form.get("refresh_token") or "", client_id=client.client_id
        )
    except FlowError as e:
        return _token_error("invalid_grant", str(e))
    row = fresh.row
    user = db.get(User, row.user_id)
    server = oauth_tokens.server_name_from_resource(row.resource)
    if user is None or server is None:
        return _token_error("invalid_grant", "grant no longer valid")
    allowed = oauth_tokens.allowed_scopes_for(db, user, server)
    if allowed is None:
        return _token_error("invalid_grant", "access to this server was revoked")
    scopes = oauth_tokens.grant_scopes(allowed, list(row.scopes or []) or None)
    resp = JSONResponse(
        {
            "access_token": oauth_tokens.mint_access_token(
                db, user_sub=user.email, client_id=client.client_id,
                server_name=server, scopes=scopes,
            )[0],
            "token_type": "Bearer",
            "expires_in": get_settings().oauth_access_token_ttl_seconds,
            "scope": " ".join(scopes),
            "refresh_token": fresh.token,
        },
        headers={"Cache-Control": "no-store"},
    )
    return resp


def _grant_jwt_bearer(request: Request, db: Session, client, form: dict):
    assertion = form.get("assertion") or ""
    if not assertion:
        return _token_error("invalid_request", "assertion is required")
    server = oauth_tokens.server_name_from_resource(form.get("resource") or "")
    if not server:
        return _token_error(
            "invalid_target",
            "resource must name one MCP server, e.g. "
            f"{oauth_tokens.issuer()}/s/<name>/mcp",
        )
    try:
        verified = oauth_assertions.validate_assertion(db, assertion)
    except AssertionError_ as e:
        logbook.record(CONTEXT_AUTH, "oauth.token", logbook.OUTCOME_FAILURE,
                       request=request,
                       message="jwt-bearer assertion rejected",
                       detail={"client_id": client.client_id, "error": str(e)})
        return _token_error("invalid_grant", str(e))

    user = verified.user
    allowed = oauth_tokens.allowed_scopes_for(db, user, server)
    if allowed is None:
        return _token_error("invalid_grant",
                            f"user has no access to server {server}")
    requested = [s for s in (form.get("scope") or "").split() if s]
    scopes = oauth_tokens.grant_scopes(allowed, requested)
    # ID-JAG: the assertion's own scope claim is an IdP-imposed ceiling.
    if verified.profile.name == "id-jag":
        ceiling = [s for s in str(verified.claims.get("scope") or "").split() if s]
        if ceiling:
            scopes = [s for s in scopes if s in set(ceiling)]
    logbook.record(CONTEXT_AUTH, "oauth.token", logbook.OUTCOME_SUCCESS,
                   request=request, user=user,
                   message=f"Access token issued for {server} (jwt-bearer, "
                           f"profile {verified.profile.name})",
                   detail={"client_id": client.client_id, "server": server,
                           "grant": "jwt-bearer", "profile": verified.profile.name,
                           "scopes": scopes})
    # No refresh token: the assertion itself is re-presentable (cache per
    # (user, server) and re-exchange on expiry — design §7).
    return _token_response(db, user=user, client=client, server=server,
                           scopes=scopes, include_refresh=False)


# ---------- DCR / introspection / revocation ----------


@router.post("/oauth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    if (get_setting(db, SETTING_OAUTH_DCR_ENABLED, "") or "").lower() == "false":
        return JSONResponse(
            {"error": "invalid_client_metadata",
             "error_description": "dynamic registration is disabled on this platform"},
            status_code=403,
        )
    try:
        metadata = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_client_metadata",
                             "error_description": "body must be JSON"}, status_code=400)
    try:
        client, secret = oauth_clients.register_dcr_client(db, metadata or {})
    except ClientRegistrationError as e:
        return JSONResponse({"error": "invalid_client_metadata",
                             "error_description": str(e)}, status_code=400)
    logbook.record(CONTEXT_AUTH, "oauth.register", logbook.OUTCOME_INFO,
                   request=request,
                   message=f"DCR client registered: {client.client_name or client.client_id}",
                   detail={"client_id": client.client_id})
    body = {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
        "token_endpoint_auth_method": client.token_endpoint_auth_method,
    }
    if secret:
        body["client_secret"] = secret  # shown once; we store only the hash
    return JSONResponse(body, status_code=201)


@router.post("/oauth/introspect")
async def introspect(request: Request, db: Session = Depends(get_db)):
    form = dict((await request.form()).items())
    client_id, client_secret = await _client_credentials(request, form)
    auth = oauth_clients.authenticate_client(db, client_id=client_id,
                                             client_secret=client_secret)
    if auth is None or not auth.authenticated:
        return _token_error("invalid_client",
                            "introspection requires a confidential client", 401)
    from app.services.oauth_tokens import OAuthTokenError, verify_access_token

    try:
        claims = verify_access_token(db, form.get("token") or "")
    except OAuthTokenError:
        return JSONResponse({"active": False})
    return JSONResponse({"active": True, **claims})


@router.post("/oauth/revoke")
async def revoke(request: Request, db: Session = Depends(get_db)):
    form = dict((await request.form()).items())
    client_id, client_secret = await _client_credentials(request, form)
    auth = oauth_clients.authenticate_client(db, client_id=client_id,
                                             client_secret=client_secret)
    if auth is None:
        return _token_error("invalid_client", "unknown client", 401)
    oauth_flows.revoke_refresh(db, token=form.get("token") or "",
                               client_id=auth.client.client_id)
    # RFC 7009: 200 regardless — revoking an unknown token is not an error.
    return JSONResponse({})
