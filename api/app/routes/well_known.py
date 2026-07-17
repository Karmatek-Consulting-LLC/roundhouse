"""Discovery endpoints: how a strange client finds the front door.

    /.well-known/oauth-authorization-server      RFC 8414 AS metadata
    /.well-known/openid-configuration            OIDC-discovery alias (same doc;
                                                 enterprise tooling probes this)
    /.well-known/jwks.json                       our public signing keys
    /.well-known/oauth-protected-resource/...    RFC 9728 PRM, one per server

PRM is served at the RFC 9728 path-insertion location for the resource
`<base>/s/{name}/mcp` — i.e. `/.well-known/oauth-protected-resource/s/{name}/mcp`
— which is exactly where a client that got a bare 401 (no resource_metadata
hint) probes by convention. One platform, many servers: each document names its
own canonical resource URL, which is how fifty servers on one host get fifty
distinct token audiences (field guide §04).

These routes are registered before the SPA catch-all mount, so they win even
though they're not under /api/.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Server
from app.services import oauth_keys, oauth_tokens

router = APIRouter(tags=["oauth-discovery"])


def _as_metadata() -> dict:
    issuer = oauth_tokens.issuer()
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "introspection_endpoint": f"{issuer}/oauth/introspect",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": [
            "authorization_code",
            "refresh_token",
            "urn:ietf:params:oauth:grant-type:jwt-bearer",
        ],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_basic",
            "client_secret_post",
        ],
        # RFC 8707: clients SHOULD send resource=; we also bind server-side.
        "resource_indicators_supported": True,
        "scopes_supported": [],
        "service_documentation": "https://github.com/Karmatek-Consulting-LLC/roundhouse",
    }


@router.get("/.well-known/oauth-authorization-server")
def oauth_as_metadata() -> dict:
    return _as_metadata()


@router.get("/.well-known/openid-configuration")
def openid_configuration_alias() -> dict:
    # Served for probe-compatibility. We are an OAuth AS, not a full OIDC OP:
    # no id_token issuance, so no userinfo endpoint — but issuer/jwks/endpoints
    # are what the probing tooling actually wants.
    return _as_metadata()


@router.get("/.well-known/jwks.json")
def jwks(db: Session = Depends(get_db)) -> dict:
    return oauth_keys.public_jwks(db)


@router.get("/.well-known/oauth-protected-resource/s/{name}/mcp")
@router.get("/.well-known/oauth-protected-resource/s/{name}")
def protected_resource_metadata(name: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Server, name) is None:
        raise HTTPException(status_code=404, detail="Unknown server")
    return {
        "resource": oauth_tokens.resource_url(name),
        "authorization_servers": [oauth_tokens.issuer()],
        "scopes_supported": oauth_tokens.registered_scopes(db, name),
        "bearer_methods_supported": ["header"],  # Authorization header only (OAuth 2.1)
        "resource_name": name,
    }
