"""Admin surface for the authorization server (superadmin-only, /api/*).

    GET/POST/DELETE /api/oauth/clients      manual client registration (the
                                            Valkyrie path: trusted confidential
                                            clients; secret shown once)
    GET/PUT  /api/oauth/assertion-profiles  the jwt-bearer profile table
                                            (docs/mcp-auth-id-jag.md §7)
    POST     /api/oauth/rotate-key          signing key rotation
    POST     /api/oauth/dev/mint            hand-testing: mint a real access
                                            token for one server as yourself —
                                            slice-1 curl fodder, admin-gated
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_superadmin
from app.models import OAuthClient, Server, User
from app.platform_settings import (
    SETTING_OAUTH_ASSERTION_PROFILES,
    SETTING_OAUTH_DCR_ENABLED,
    get_setting,
    put_setting,
)
from app.services import oauth_keys, oauth_tokens
from app.services.oauth_clients import create_manual_client

router = APIRouter(prefix="/api/oauth", tags=["oauth-admin"])


def _client_out(c: OAuthClient) -> dict:
    return {
        "client_id": c.client_id,
        "client_name": c.client_name,
        "token_endpoint_auth_method": c.token_endpoint_auth_method,
        "redirect_uris": c.redirect_uris or [],
        "grant_types": c.grant_types or [],
        "registration_type": c.registration_type,
        "trusted": c.trusted,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/clients")
def list_clients(db: Session = Depends(get_db),
                 _: User = Depends(require_superadmin)) -> list[dict]:
    rows = db.query(OAuthClient).order_by(OAuthClient.created_at).all()
    return [_client_out(c) for c in rows]


class ClientCreate(BaseModel):
    client_name: str = Field(min_length=1, max_length=255)
    trusted: bool = False
    confidential: bool = True
    redirect_uris: list[str] = []
    grant_types: list[str] | None = None


@router.post("/clients")
def create_client(payload: ClientCreate, db: Session = Depends(get_db),
                  _: User = Depends(require_superadmin)) -> dict:
    client, secret = create_manual_client(
        db,
        client_name=payload.client_name,
        trusted=payload.trusted,
        confidential=payload.confidential,
        redirect_uris=payload.redirect_uris,
        grant_types=payload.grant_types,
    )
    out = _client_out(client)
    if secret:
        out["client_secret"] = secret  # once, at creation — only the hash is stored
    return out


@router.delete("/clients/{client_id:path}")
def delete_client(client_id: str, db: Session = Depends(get_db),
                  _: User = Depends(require_superadmin)) -> dict:
    row = db.get(OAuthClient, client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown client")
    db.delete(row)
    return {"deleted": client_id}


@router.get("/assertion-profiles")
def get_assertion_profiles(db: Session = Depends(get_db),
                           _: User = Depends(require_superadmin)) -> dict:
    from app.services.oauth_assertions import load_profiles

    return {
        "raw": get_setting(db, SETTING_OAUTH_ASSERTION_PROFILES, "") or "",
        "effective": [p.__dict__ for p in load_profiles(db)],
    }


class ProfilesUpdate(BaseModel):
    profiles: list[dict]


@router.put("/assertion-profiles")
def put_assertion_profiles(payload: ProfilesUpdate, db: Session = Depends(get_db),
                           _: User = Depends(require_superadmin)) -> dict:
    put_setting(db, SETTING_OAUTH_ASSERTION_PROFILES, json.dumps(payload.profiles))
    from app.services.oauth_assertions import load_profiles

    return {"effective": [p.__dict__ for p in load_profiles(db)]}


class DcrToggle(BaseModel):
    enabled: bool


@router.put("/dcr")
def toggle_dcr(payload: DcrToggle, db: Session = Depends(get_db),
               _: User = Depends(require_superadmin)) -> dict:
    put_setting(db, SETTING_OAUTH_DCR_ENABLED, "true" if payload.enabled else "false")
    return {"enabled": payload.enabled}


@router.post("/rotate-key")
def rotate_key(db: Session = Depends(get_db),
               _: User = Depends(require_superadmin)) -> dict:
    return {"kid": oauth_keys.rotate_signing_key(db)}


class DevMint(BaseModel):
    server: str
    scopes: list[str] | None = None
    ttl_seconds: int | None = None


@router.post("/dev/mint")
def dev_mint(payload: DevMint, db: Session = Depends(get_db),
             admin: User = Depends(require_superadmin)) -> dict:
    """Slice-1 lab endpoint: a real, signed, audience-bound token for one
    server, issued to you. Exists so the token plane is curl-testable before
    any OAuth flow is wired up; superadmin-only on purpose."""
    if db.get(Server, payload.server) is None:
        raise HTTPException(status_code=404, detail="Unknown server")
    allowed = oauth_tokens.registered_scopes(db, payload.server)
    scopes = oauth_tokens.grant_scopes(allowed, payload.scopes)
    token, claims = oauth_tokens.mint_access_token(
        db,
        user_sub=admin.email,
        client_id="dev-mint",
        server_name=payload.server,
        scopes=scopes,
        ttl_seconds=payload.ttl_seconds,
    )
    return {"access_token": token, "claims": claims}
