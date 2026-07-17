"""OAuth 2.1 authorization server: keys, discovery, PKCE code flow, refresh
rotation, DCR, jwt-bearer assertion profiles, and the codegen verifier chain.

Route tests drive the real FastAPI app through TestClient with get_db
overridden onto an in-memory SQLite session — the same "real query paths,
no Postgres" approach as test_sso.py.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import secrets
import time
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.jose import JsonWebKey, JsonWebToken
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register tables on Base.metadata
from app.auth import hash_password
from app.config import get_settings
from app.db import Base, get_db
from app.models import OAuthRefreshToken, Server, ServerOwner, ServerScope, User

_scope_ids = itertools.count(1)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv(
        "APP_KEY", "base64:" + base64.b64encode(os.urandom(32)).decode()
    )
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, autoflush=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        get_settings.cache_clear()


@pytest.fixture
def client(db):
    from app.main import app as fastapi_app

    def _override():
        yield db
        db.commit()

    fastapi_app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(fastapi_app, follow_redirects=False)
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def admin(db):
    user = User(
        email="admin@test.local",
        password_hash=hash_password("pw"),
        display_name="Admin",
        role="superadmin",
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def server(db, admin):
    db.add(Server(name="net-tools", spec={}, mode="structured"))
    db.add(ServerOwner(server_name="net-tools", owner_id=admin.id))
    for s in ("tools:ping", "tools:traceroute"):
        db.add(ServerScope(id=next(_scope_ids), server_name="net-tools", name=s))
    db.flush()
    return "net-tools"


def _base() -> str:
    return get_settings().mcp_base_url.rstrip("/")


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


# ---------- keys / metadata / PRM ----------


def test_signing_key_and_jwks(db):
    from app.services import oauth_keys

    jwks = oauth_keys.public_jwks(db)
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kty"] == "RSA" and key["kid"].startswith("rh-")
    assert "d" not in key  # never the private half

    old_kid = key["kid"]
    new_kid = oauth_keys.rotate_signing_key(db)
    jwks = oauth_keys.public_jwks(db)
    kids = {k["kid"] for k in jwks["keys"]}
    # Old public key still served so unexpired tokens keep validating.
    assert kids == {old_kid, new_kid}


def test_as_metadata_and_alias(client):
    doc = client.get("/.well-known/oauth-authorization-server").json()
    assert doc["issuer"] == _base()
    assert doc["token_endpoint"].endswith("/oauth/token")
    assert "urn:ietf:params:oauth:grant-type:jwt-bearer" in doc["grant_types_supported"]
    assert doc["code_challenge_methods_supported"] == ["S256"]
    alias = client.get("/.well-known/openid-configuration").json()
    assert alias["issuer"] == doc["issuer"]


def test_prm_per_server(client, server):
    doc = client.get(f"/.well-known/oauth-protected-resource/s/{server}/mcp").json()
    assert doc["resource"] == f"{_base()}/s/{server}/mcp"
    assert doc["authorization_servers"] == [_base()]
    assert doc["scopes_supported"] == ["tools:ping", "tools:traceroute"]
    assert client.get(
        "/.well-known/oauth-protected-resource/s/nope/mcp"
    ).status_code == 404


# ---------- token plane ----------


def test_mint_and_verify_audience_binding(db, admin, server):
    from app.services import oauth_tokens

    token, claims = oauth_tokens.mint_access_token(
        db, user_sub=admin.email, client_id="c", server_name=server,
        scopes=["tools:ping"],
    )
    ok = oauth_tokens.verify_access_token(
        db, token, audience=f"{_base()}/s/{server}/mcp"
    )
    assert ok["sub"] == admin.email and ok["scope"] == "tools:ping"

    with pytest.raises(oauth_tokens.OAuthTokenError):
        oauth_tokens.verify_access_token(
            db, token, audience=f"{_base()}/s/other-server/mcp"
        )


def test_resource_parsing(db):
    from app.services.oauth_tokens import server_name_from_resource

    assert server_name_from_resource(f"{_base()}/s/net-tools/mcp") == "net-tools"
    assert server_name_from_resource(f"{_base()}/s/net-tools") == "net-tools"
    assert server_name_from_resource("https://elsewhere/s/x/mcp") is None
    assert server_name_from_resource("") is None


def test_dev_mint_endpoint(client, db, admin, server):
    from app.deps import require_superadmin
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[require_superadmin] = lambda: admin
    try:
        resp = client.post(
            "/api/oauth/dev/mint",
            json={"server": server, "scopes": ["tools:ping"]},
        )
    finally:
        fastapi_app.dependency_overrides.pop(require_superadmin, None)
    assert resp.status_code == 200
    body = resp.json()
    assert body["claims"]["aud"] == f"{_base()}/s/{server}/mcp"
    assert body["claims"]["scope"] == "tools:ping"


# ---------- DCR ----------


def test_dcr_registration(client):
    resp = client.post(
        "/oauth/register",
        json={"client_name": "VS Code", "redirect_uris": ["http://localhost:33418"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["client_id"].startswith("rhc_")
    assert body["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in body


def test_dcr_requires_redirect_uris(client):
    resp = client.post("/oauth/register", json={"client_name": "x"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_client_metadata"


def test_dcr_can_be_disabled(client, db):
    from app.platform_settings import SETTING_OAUTH_DCR_ENABLED, put_setting

    put_setting(db, SETTING_OAUTH_DCR_ENABLED, "false")
    db.flush()
    resp = client.post(
        "/oauth/register",
        json={"client_name": "x", "redirect_uris": ["https://x/cb"]},
    )
    assert resp.status_code == 403


# ---------- interactive flow, end to end ----------


def _register_client(client) -> str:
    return client.post(
        "/oauth/register",
        json={"client_name": "lab", "redirect_uris": ["http://localhost:9999/cb"]},
    ).json()["client_id"]


def _authorize_query(client_id: str, challenge: str, server: str) -> dict:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://localhost:9999/cb",
        "scope": "tools:ping",
        "state": "st4te",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": f"{_base()}/s/{server}/mcp",
    }


def _extract_form_blob(html_text: str) -> str:
    marker = 'name="request" value="'
    start = html_text.index(marker) + len(marker)
    return html_text[start : html_text.index('"', start)]


def test_full_code_flow(client, db, admin, server):
    client_id = _register_client(client)
    verifier, challenge = _pkce()

    # 1. authorize with no session -> login page
    r = client.get("/oauth/authorize", params=_authorize_query(client_id, challenge, server))
    assert r.status_code == 200 and "Sign in" in r.text
    blob = _extract_form_blob(r.text)

    # 2. local login -> consent page (client is not trusted), session cookie set
    r = client.post(
        "/oauth/authorize",
        data={"email": admin.email, "password": "pw", "request": blob},
    )
    assert r.status_code == 200 and "Authorize" in r.text
    assert "rh_as_session" in r.cookies
    blob = _extract_form_blob(r.text)

    # 3. approve -> redirect with code + echoed state
    r = client.post("/oauth/consent", data={"action": "approve", "request": blob})
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["st4te"]
    code = q["code"][0]

    # 4. redeem the code (public client: PKCE is the whole proof)
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:9999/cb",
            "code_verifier": verifier,
            "client_id": client_id,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "tools:ping"
    from app.services.oauth_tokens import verify_access_token

    claims = verify_access_token(db, body["access_token"],
                                 audience=f"{_base()}/s/{server}/mcp")
    assert claims["sub"] == admin.email
    assert claims["client_id"] == client_id

    # 5. code is single-use
    r2 = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:9999/cb",
            "code_verifier": verifier,
            "client_id": client_id,
        },
    )
    assert r2.status_code == 400 and r2.json()["error"] == "invalid_grant"

    # 6. second authorize on the same session: no login, no consent — silent
    verifier2, challenge2 = _pkce()
    r = client.get("/oauth/authorize",
                   params=_authorize_query(client_id, challenge2, server))
    assert r.status_code == 302 and "code=" in r.headers["location"]

    # 7. refresh rotation
    refresh = body["refresh_token"]
    r = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh,
              "client_id": client_id},
    )
    assert r.status_code == 200
    rotated = r.json()["refresh_token"]
    assert rotated != refresh

    # 8. reusing the retired refresh token trips theft detection and kills the family
    r = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh,
              "client_id": client_id},
    )
    assert r.status_code == 400
    r = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": rotated,
              "client_id": client_id},
    )
    assert r.status_code == 400  # family revoked
    live = db.query(OAuthRefreshToken).filter(
        OAuthRefreshToken.revoked_at.is_(None)).count()
    assert live == 0


def test_pkce_wrong_verifier_rejected(client, db, admin, server):
    client_id = _register_client(client)
    _, challenge = _pkce()
    r = client.get("/oauth/authorize",
                   params=_authorize_query(client_id, challenge, server))
    blob = _extract_form_blob(r.text)
    r = client.post("/oauth/authorize",
                    data={"email": admin.email, "password": "pw", "request": blob})
    blob = _extract_form_blob(r.text)
    r = client.post("/oauth/consent", data={"action": "approve", "request": blob})
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:9999/cb",
            "code_verifier": "totally-wrong-verifier-aaaaaaaaaaaaaaaaaaaaaa",
            "client_id": client_id,
        },
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_unknown_redirect_uri_never_redirects(client, db, admin, server):
    client_id = _register_client(client)
    _, challenge = _pkce()
    q = _authorize_query(client_id, challenge, server)
    q["redirect_uri"] = "http://evil.example/steal"
    r = client.get("/oauth/authorize", params=q)
    assert r.status_code == 400  # error page — never a redirect to evil
    assert "not registered" in r.text


# ---------- jwt-bearer (the interim Valkyrie path) ----------


@pytest.fixture
def idp_key():
    return JsonWebKey.generate_key("RSA", 2048, {"kid": "idp1"}, is_private=True)


@pytest.fixture
def fake_idp(monkeypatch, db, idp_key):
    """Point the assertion validator at a locally-signed 'Entra': profile
    config via platform_settings, JWKS via a patched validator client."""
    from app.platform_settings import SETTING_OAUTH_ASSERTION_PROFILES, put_setting
    from app.services import oauth_assertions

    put_setting(
        db,
        SETTING_OAUTH_ASSERTION_PROFILES,
        json.dumps([
            {"name": "entra-id-token", "enabled": True,
             "issuer": "https://idp.test/v2.0",
             "audience": "valkyrie-client-id",
             "discovery_url": "https://idp.test/.well-known/openid-configuration"},
        ]),
    )
    db.flush()
    real = oauth_assertions._idp_validator

    def patched(profile):
        client = real(profile)
        monkeypatch.setattr(
            client, "_jwks_keys",
            lambda force=False: JsonWebKey.import_key_set(
                {"keys": [idp_key.as_dict(is_private=False)]}
            ),
        )
        return client

    monkeypatch.setattr(oauth_assertions, "_idp_validator", patched)
    return idp_key


def _entra_id_token(key, *, sub="entra-sub-1", email="alice@test.local",
                    aud="valkyrie-client-id", iss="https://idp.test/v2.0",
                    typ=None, jti=None, ttl=3600):
    now = int(time.time())
    header = {"alg": "RS256", "kid": "idp1"}
    if typ:
        header["typ"] = typ
    claims = {
        "iss": iss, "aud": aud, "sub": sub, "oid": sub,
        "preferred_username": email, "iat": now, "exp": now + ttl,
    }
    if jti:
        claims["jti"] = jti
    tok = JsonWebToken(["RS256"]).encode(header, claims, key)
    return tok.decode() if isinstance(tok, bytes) else tok


@pytest.fixture
def valkyrie(db):
    from app.services.oauth_clients import create_manual_client

    row, secret = create_manual_client(
        db, client_name="valkyrie", trusted=True, confidential=True
    )
    return row.client_id, secret


@pytest.fixture
def alice(db):
    user = User(email="alice@test.local", display_name="Alice", role="user",
                auth_source="entra", oidc_sub="entra-sub-1")
    db.add(user)
    db.flush()
    return user


def test_jwt_bearer_exchange(client, db, server, fake_idp, valkyrie, alice, admin):
    # Alice needs access: put her on the owner's "team" by making her the
    # owner's teammate is complex — simplest true-to-life path: she owns it.
    db.query(ServerOwner).filter(ServerOwner.server_name == server).delete()
    db.add(ServerOwner(server_name=server, owner_id=alice.id))
    db.flush()

    cid, secret = valkyrie
    assertion = _entra_id_token(fake_idp)
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
            "resource": f"{_base()}/s/{server}/mcp",
            "scope": "tools:ping",
        },
        auth=(cid, secret),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "refresh_token" not in body  # assertion is re-presentable instead
    from app.services.oauth_tokens import verify_access_token

    claims = verify_access_token(db, body["access_token"],
                                 audience=f"{_base()}/s/{server}/mcp")
    assert claims["sub"] == "alice@test.local"
    assert claims["client_id"] == cid
    assert claims["scope"] == "tools:ping"


def test_jwt_bearer_requires_trusted_client(client, db, server, fake_idp, alice):
    dcr_id = _register_client(client)
    assertion = _entra_id_token(fake_idp)
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
            "resource": f"{_base()}/s/{server}/mcp",
            "client_id": dcr_id,
        },
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unauthorized_client"


def test_jwt_bearer_wrong_audience_rejected(client, db, server, fake_idp,
                                            valkyrie, alice):
    cid, secret = valkyrie
    assertion = _entra_id_token(fake_idp, aud="some-other-app")
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
            "resource": f"{_base()}/s/{server}/mcp",
        },
        auth=(cid, secret),
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_jwt_bearer_unknown_subject_rejected(client, db, server, fake_idp, valkyrie):
    cid, secret = valkyrie
    assertion = _entra_id_token(fake_idp, sub="ghost", email="ghost@test.local")
    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
            "resource": f"{_base()}/s/{server}/mcp",
        },
        auth=(cid, secret),
    )
    assert r.status_code == 400 and "not a Roundhouse user" in r.json()["error_description"]


def test_id_jag_profile_single_use(client, db, server, monkeypatch, idp_key,
                                   valkyrie, alice):
    """The future profile, exercised today: typ-checked, aud = our issuer,
    jti single-use."""
    from app.platform_settings import SETTING_OAUTH_ASSERTION_PROFILES, put_setting
    from app.services import oauth_assertions
    from app.services.oauth_tokens import issuer

    db.query(ServerOwner).filter(ServerOwner.server_name == server).delete()
    db.add(ServerOwner(server_name=server, owner_id=alice.id))
    put_setting(
        db,
        SETTING_OAUTH_ASSERTION_PROFILES,
        json.dumps([
            {"name": "id-jag", "enabled": True,
             "issuer": "https://idp.test/v2.0",
             "discovery_url": "https://idp.test/.well-known/openid-configuration"},
        ]),
    )
    db.flush()
    real = oauth_assertions._idp_validator

    def patched(profile):
        c = real(profile)
        monkeypatch.setattr(
            c, "_jwks_keys",
            lambda force=False: JsonWebKey.import_key_set(
                {"keys": [idp_key.as_dict(is_private=False)]}
            ),
        )
        return c

    monkeypatch.setattr(oauth_assertions, "_idp_validator", patched)

    cid, secret = valkyrie
    jag = _entra_id_token(idp_key, aud=issuer(), typ="oauth-id-jag+jwt",
                          jti="jag-001", ttl=300)
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jag,
        "resource": f"{_base()}/s/{server}/mcp",
    }
    r = client.post("/oauth/token", data=data, auth=(cid, secret))
    assert r.status_code == 200, r.text

    # Replay: same letter of introduction, second visit -> refused.
    r = client.post("/oauth/token", data=data, auth=(cid, secret))
    assert r.status_code == 400 and "replay" in r.json()["error_description"]

    # An id_token without the id-jag typ must not satisfy the id-jag profile.
    plain = _entra_id_token(idp_key, aud=issuer(), jti="jag-002")
    r = client.post("/oauth/token", data={**data, "assertion": plain},
                    auth=(cid, secret))
    assert r.status_code == 400


# ---------- codegen: the verifier chain in generated servers ----------


def test_codegen_emits_multiauth_chain():
    from app.services import codegen
    from app.services.spec import ServerSpec

    spec = ServerSpec(
        name="net-tools",
        primitives=[{"kind": "tool", "name": "ping", "scopes": ["tools:ping"],
                     "code": "return 1"}],
        tokens=[{"name": "legacy", "token": "mcps_x", "scopes": ["tools:ping"]}],
    )
    src = codegen.generate_server_py(spec, format_output=False)
    assert "StaticTokenVerifier" in src            # old tokens keep working
    assert "_RhMultiVerifier" in src               # chained
    assert "_RhJwtVerifier" in src
    assert "/s/net-tools/mcp" in src               # audience baked per server
    compile(src, "<generated>", "exec")            # emitted code is valid python

    proxy_spec = ServerSpec(
        name="elastic", mode="remote", remote_url="https://x/mcp",
        primitives=[], tokens=[{"name": "t", "token": "mcps_y", "scopes": []}],
    )
    proxy_src = codegen.generate_proxy_py(proxy_spec, format_output=False)
    assert "_RhMultiVerifier" in proxy_src
    compile(proxy_src, "<generated-proxy>", "exec")


def test_codegen_no_tokens_stays_open():
    from app.services import codegen
    from app.services.spec import ServerSpec

    spec = ServerSpec(name="open", primitives=[{"kind": "tool", "name": "t",
                                                "code": "return 1"}])
    src = codegen.generate_server_py(spec, format_output=False)
    assert "_RhMultiVerifier" not in src
    assert "StaticTokenVerifier" not in src


def test_dockerfile_adds_authlib_when_auth_enabled():
    from app.services import codegen
    from app.services.spec import ServerSpec

    spec = ServerSpec(
        name="s", primitives=[],
        tokens=[{"name": "t", "token": "mcps_z", "scopes": []}],
    )
    df = codegen.generate_dockerfile(spec)
    assert "authlib" in df
