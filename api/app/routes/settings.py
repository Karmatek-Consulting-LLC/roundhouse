from __future__ import annotations

import re
import ssl

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_superadmin
from app.models import User
from app.platform_settings import (
    SETTING_CUSTOM_CA_CERT,
    SETTING_DOCKER_REGISTRY,
    SETTING_DOCKER_REGISTRY_PASSWORD,
    SETTING_DOCKER_REGISTRY_USERNAME,
    get_setting,
    put_setting,
)
from app.services import global_env, sso_config
from app.services.docker import get_docker
from app.services.server_service import get_server_service
from app.services.spec import EnvVar

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_superadmin)])


def _base_url() -> str:
    # Single source of truth: the public base URL is set at deploy time via
    # MCP_BASE_URL (derived from PUBLIC_HOSTNAME, the same value Traefik routes
    # on). It is read-only in the UI; changing it is a redeploy. Defaults to
    # http://localhost:3080 when unset.
    return get_settings().mcp_base_url


def _registry_prefix(db: Session) -> str:
    raw = (get_setting(db, SETTING_DOCKER_REGISTRY, "") or "").strip()
    return raw.rstrip("/") if raw else ""


def _password_configured(db: Session) -> bool:
    return bool((get_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD, "") or "").strip())


@router.get("")
def index(_: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    cfg = get_settings()
    return {
        # Read-only: the public base URL is set at deploy time (MCP_BASE_URL /
        # PUBLIC_HOSTNAME), not editable from the UI.
        "base_url": _base_url(),
        "default_mcp_server_replicas": cfg.mcp_default_server_replicas,
        "max_mcp_server_replicas": cfg.mcp_max_server_replicas,
        "orchestrator": get_docker().mode(),
        "supports_scaling": get_docker().supports_scaling(),
        "docker_swarm_mode": get_docker().supports_scaling(),
        "docker_registry": (get_setting(db, SETTING_DOCKER_REGISTRY, "") or "").strip(),
        "docker_registry_effective": _registry_prefix(db),
        "docker_registry_username": get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, "") or "",
        "docker_registry_password_configured": _password_configured(db),
        "custom_ca_cert_configured": bool(
            (get_setting(db, SETTING_CUSTOM_CA_CERT, "") or "").strip()
        ),
        "custom_ca_cert_count": _count_certs(get_setting(db, SETTING_CUSTOM_CA_CERT, "") or ""),
    }


class RegistryIn(BaseModel):
    registry: str = ""
    username: str = ""
    password: str | None = None


@router.put("/docker-registry")
def update_docker_registry(payload: RegistryIn, db: Session = Depends(get_db)):
    registry = (payload.registry or "").strip()
    put_setting(db, SETTING_DOCKER_REGISTRY, registry)
    put_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, (payload.username or "").strip())
    if payload.password is not None:
        put_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD, payload.password or "")
    return {
        "docker_registry": registry,
        "docker_registry_effective": _registry_prefix(db),
        "docker_registry_username": get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, "") or "",
        "docker_registry_password_configured": _password_configured(db),
    }


class CustomCaIn(BaseModel):
    cert: str = Field(min_length=1, max_length=262144)


def _count_certs(pem: str) -> int:
    """Number of PEM certificate blocks. The field is a BUNDLE - paste a CA per
    upstream (each its full chain) and they're all trusted."""
    return len(re.findall(r"-----BEGIN CERTIFICATE-----", pem))


@router.put("/custom-ca")
def update_custom_ca(payload: CustomCaIn, db: Session = Depends(get_db)):
    # The field is a trust bundle: validate it parses and count the certs so the
    # UI can confirm what was loaded (multiple CAs / full chains are supported).
    from app.services.mcp_client import verify_for_ca

    try:
        verify_for_ca(payload.cert)
    except ssl.SSLError as e:
        raise HTTPException(
            status_code=422, detail=f"Not a valid PEM certificate bundle: {e}"
        ) from e
    count = _count_certs(payload.cert)
    if count == 0:
        raise HTTPException(
            status_code=422,
            detail="No certificate found. Paste one or more PEM blocks "
            "(-----BEGIN CERTIFICATE----- … -----END CERTIFICATE-----).",
        )
    put_setting(db, SETTING_CUSTOM_CA_CERT, payload.cert)
    return {"custom_ca_cert_configured": True, "cert_count": count}


@router.delete("/custom-ca")
def delete_custom_ca(db: Session = Depends(get_db)):
    put_setting(db, SETTING_CUSTOM_CA_CERT, "")
    return {"custom_ca_cert_configured": False}


def _suggested_redirect_uri() -> str:
    # The callback the SPA's OIDC flow uses; offered as a default in the UI so it
    # matches what must be registered on the Entra app.
    return f"{_base_url().rstrip('/')}/api/auth/oidc/callback"


@router.get("/sso")
def get_sso(db: Session = Depends(get_db)):
    cfg = sso_config.load(db)
    return {
        "entra_tenant_id": cfg.tenant_id,
        "entra_client_id": cfg.client_id,
        # Never return the secret; only whether one is stored.
        "entra_client_secret_configured": sso_config.secret_configured(db),
        "entra_redirect_uri": cfg.redirect_uri,
        "suggested_redirect_uri": _suggested_redirect_uri(),
        "enabled": cfg.enabled,
    }


class SsoConfigIn(BaseModel):
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_redirect_uri: str = ""
    # Write-only: omit/None keeps the stored secret, "" clears it, else replaces.
    entra_client_secret: str | None = None


@router.put("/sso")
def update_sso(payload: SsoConfigIn, db: Session = Depends(get_db)):
    sso_config.save(
        db,
        tenant_id=payload.entra_tenant_id,
        client_id=payload.entra_client_id,
        redirect_uri=payload.entra_redirect_uri,
        client_secret=payload.entra_client_secret,
    )
    db.flush()
    cfg = sso_config.load(db)
    return {
        "entra_tenant_id": cfg.tenant_id,
        "entra_client_id": cfg.client_id,
        "entra_client_secret_configured": sso_config.secret_configured(db),
        "entra_redirect_uri": cfg.redirect_uri,
        "suggested_redirect_uri": _suggested_redirect_uri(),
        "enabled": cfg.enabled,
    }


@router.get("/mcp-env")
def get_mcp_env(db: Session = Depends(get_db)):
    return {"env_vars": [ev.to_dict() for ev in global_env.list_globals(db)]}


class EnvVarIn(BaseModel):
    name: str = Field(min_length=1)
    value: str = ""


class McpEnvIn(BaseModel):
    env_vars: list[EnvVarIn] = []


@router.put("/mcp-env")
def put_mcp_env(payload: McpEnvIn, db: Session = Depends(get_db)):
    vars_: list[EnvVar] = []
    for item in payload.env_vars:
        ev = EnvVar.from_dict(item.model_dump())
        if ev is not None:
            vars_.append(ev)
    global_env.save_globals(db, vars_)
    # Flush so reapply_runtime_env reads what we just wrote.
    db.flush()
    get_server_service().reapply_runtime_env_for_all_servers(db)
    return {"env_vars": [v.to_dict() for v in global_env.list_globals(db)]}
