from __future__ import annotations

import re
import ssl

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.config import get_settings
from app.db import get_db
from app.deps import require_superadmin
from app.models import User
from app.platform_settings import (
    SETTING_BASE_REGISTRY,
    SETTING_BASE_REGISTRY_PASSWORD,
    SETTING_BASE_REGISTRY_USERNAME,
    SETTING_CUSTOM_CA_CERT,
    SETTING_DOCKER_REGISTRY,
    SETTING_DOCKER_REGISTRY_PASSWORD,
    SETTING_DOCKER_REGISTRY_USERNAME,
    SETTING_MCP_BASE_BUILD_IMAGE,
    SETTING_MCP_BASE_RUNTIME_IMAGE,
    SETTING_REGISTRY_SCANNER,
    SETTING_REGISTRY_SCANNER_API_URL,
    get_setting,
    put_setting,
)
from app.services import global_env, sso_config, tls_cert
from app.services.docker import get_docker
from app.services.docker_http import DockerError
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


def _base_registry_password_configured(db: Session) -> bool:
    return bool((get_setting(db, SETTING_BASE_REGISTRY_PASSWORD, "") or "").strip())


def _effective_base_images(db: Session) -> tuple[str, str]:
    cfg = get_settings()
    build = (get_setting(db, SETTING_MCP_BASE_BUILD_IMAGE, "") or "").strip()
    runtime = (get_setting(db, SETTING_MCP_BASE_RUNTIME_IMAGE, "") or "").strip()
    return build or cfg.mcp_server_build_image, runtime or cfg.mcp_server_runtime_image


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
        "tls_cert": tls_cert.status(db),
        "registry_scanner": (get_setting(db, SETTING_REGISTRY_SCANNER, "") or "").strip(),
        "registry_scanner_api_url": (get_setting(db, SETTING_REGISTRY_SCANNER_API_URL, "") or "").strip(),
        # Base image registry (pull creds for the generated servers' FROM images,
        # e.g. dhi.io). Password never returned - only whether it is configured.
        "base_registry": (get_setting(db, SETTING_BASE_REGISTRY, "") or "").strip(),
        "base_registry_username": get_setting(db, SETTING_BASE_REGISTRY_USERNAME, "") or "",
        "base_registry_password_configured": _base_registry_password_configured(db),
        # Base images for generated MCP servers. `*_configured` is the operator
        # override (blank = using the env default shown in `*_effective`).
        "mcp_base_build_image": (get_setting(db, SETTING_MCP_BASE_BUILD_IMAGE, "") or "").strip(),
        "mcp_base_runtime_image": (get_setting(db, SETTING_MCP_BASE_RUNTIME_IMAGE, "") or "").strip(),
        "mcp_base_build_image_effective": _effective_base_images(db)[0],
        "mcp_base_runtime_image_effective": _effective_base_images(db)[1],
    }


class TlsCertIn(BaseModel):
    cert: str = Field(min_length=1, max_length=262144)
    key: str = Field(min_length=1, max_length=262144)


@router.put("/tls-cert")
def update_tls_cert(
    payload: TlsCertIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    # Validate + persist + push to the ingress Traefik. A bad pair is a 422; a
    # Swarm/Docker failure is a 502 (and get_db rolls the stored cert back).
    try:
        info = tls_cert.apply_certificate(db, payload.cert, payload.key)
    except tls_cert.TlsCertError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except DockerError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Certificate is valid but applying it to Traefik failed: {e}",
        ) from e
    audit_record(db, me, "settings.tls_cert.update", "settings", "tls_cert", dict(info))
    return {"tls_cert": {"supported": True, "configured": True, **info}}


@router.delete("/tls-cert")
def delete_tls_cert(me: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    tls_cert.clear_certificate(db)
    audit_record(db, me, "settings.tls_cert.delete", "settings", "tls_cert")
    return {"tls_cert": tls_cert.status(db)}


class RegistryIn(BaseModel):
    registry: str = ""
    username: str = ""
    password: str | None = None


@router.put("/docker-registry")
def update_docker_registry(
    payload: RegistryIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    registry = (payload.registry or "").strip()
    put_setting(db, SETTING_DOCKER_REGISTRY, registry)
    put_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, (payload.username or "").strip())
    if payload.password is not None:
        put_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD, payload.password or "")
    audit_record(db, me, "settings.docker_registry.update", "settings", "docker_registry", {
        "registry": registry,
        "username": (payload.username or "").strip(),
        "password_changed": payload.password is not None,
    })
    return {
        "docker_registry": registry,
        "docker_registry_effective": _registry_prefix(db),
        "docker_registry_username": get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, "") or "",
        "docker_registry_password_configured": _password_configured(db),
    }


def _encrypt_secret(value: str) -> str:
    """Encrypt a secret at rest with the app.crypto AES envelope (like the Entra
    client secret / TLS key). Falls back to plaintext only when APP_KEY is unset
    (dev), mirroring the other secret settings."""
    from app.crypto import encrypt

    app_key = get_settings().app_key
    return encrypt(value, app_key) if app_key else value


class BaseRegistryIn(BaseModel):
    # Registry host to authenticate for base-image (FROM) pulls, e.g. "dhi.io".
    # Blank derives the host from the configured base images.
    registry: str = ""
    username: str = ""
    # None = leave unchanged; "" = clear; non-empty = set (stored encrypted).
    password: str | None = None


@router.put("/base-registry")
def update_base_registry(
    payload: BaseRegistryIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Credentials for pulling the generated servers' base images (Docker
    Hardened Images at dhi.io) at build time. The password is encrypted at rest
    and delivered to the build daemon as X-Registry-Config."""
    put_setting(db, SETTING_BASE_REGISTRY, (payload.registry or "").strip())
    put_setting(db, SETTING_BASE_REGISTRY_USERNAME, (payload.username or "").strip())
    if payload.password is not None:
        put_setting(
            db,
            SETTING_BASE_REGISTRY_PASSWORD,
            _encrypt_secret(payload.password) if payload.password else "",
        )
    audit_record(db, me, "settings.base_registry.update", "settings", "base_registry", {
        "registry": (payload.registry or "").strip(),
        "username": (payload.username or "").strip(),
        "password_changed": payload.password is not None,
    })
    return {
        "base_registry": (get_setting(db, SETTING_BASE_REGISTRY, "") or "").strip(),
        "base_registry_username": get_setting(db, SETTING_BASE_REGISTRY_USERNAME, "") or "",
        "base_registry_password_configured": _base_registry_password_configured(db),
    }


class BaseImagesIn(BaseModel):
    # Blank = fall back to the MCP_SERVER_*_IMAGE env default.
    build_image: str = ""
    runtime_image: str = ""


@router.put("/base-images")
def update_base_images(
    payload: BaseImagesIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Override the base images for generated MCP servers (build + runtime
    stages). Blank restores the env default."""
    put_setting(db, SETTING_MCP_BASE_BUILD_IMAGE, (payload.build_image or "").strip())
    put_setting(db, SETTING_MCP_BASE_RUNTIME_IMAGE, (payload.runtime_image or "").strip())
    audit_record(db, me, "settings.base_images.update", "settings", "base_images", {
        "build_image": (payload.build_image or "").strip(),
        "runtime_image": (payload.runtime_image or "").strip(),
    })
    build_eff, runtime_eff = _effective_base_images(db)
    return {
        "mcp_base_build_image": (get_setting(db, SETTING_MCP_BASE_BUILD_IMAGE, "") or "").strip(),
        "mcp_base_runtime_image": (get_setting(db, SETTING_MCP_BASE_RUNTIME_IMAGE, "") or "").strip(),
        "mcp_base_build_image_effective": build_eff,
        "mcp_base_runtime_image_effective": runtime_eff,
    }


class RegistryScannerIn(BaseModel):
    # "" disables; "harbor" reads Trivy scan overviews from the Harbor API.
    scanner: str = ""
    api_url: str = ""


@router.put("/registry-scanner")
def update_registry_scanner(
    payload: RegistryScannerIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    from app.services import registry_scan

    scanner = (payload.scanner or "").strip().lower()
    if scanner not in ("", registry_scan.SCANNER_HARBOR):
        raise HTTPException(status_code=422, detail=f"Unknown scanner: {scanner!r}")
    api_url = (payload.api_url or "").strip().rstrip("/")
    if api_url and not api_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="API URL must start with http(s)://")
    put_setting(db, SETTING_REGISTRY_SCANNER, scanner)
    put_setting(db, SETTING_REGISTRY_SCANNER_API_URL, api_url)
    registry_scan.get_scanner().invalidate()  # config changed - drop cached verdicts
    audit_record(db, me, "settings.registry_scanner.update", "settings", "registry_scanner", {
        "scanner": scanner, "api_url": api_url,
    })
    return {"registry_scanner": scanner, "registry_scanner_api_url": api_url}


class CustomCaIn(BaseModel):
    cert: str = Field(min_length=1, max_length=262144)


def _count_certs(pem: str) -> int:
    """Number of PEM certificate blocks. The field is a BUNDLE - paste a CA per
    upstream (each its full chain) and they're all trusted."""
    return len(re.findall(r"-----BEGIN CERTIFICATE-----", pem))


@router.put("/custom-ca")
def update_custom_ca(
    payload: CustomCaIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
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
    audit_record(db, me, "settings.custom_ca.update", "settings", "custom_ca", {"cert_count": count})
    return {"custom_ca_cert_configured": True, "cert_count": count}


@router.delete("/custom-ca")
def delete_custom_ca(me: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    put_setting(db, SETTING_CUSTOM_CA_CERT, "")
    audit_record(db, me, "settings.custom_ca.delete", "settings", "custom_ca")
    return {"custom_ca_cert_configured": False}


def _sso_response(db: Session) -> dict:
    cfg = sso_config.load(db)
    return {
        "entra_tenant_id": cfg.tenant_id,
        "entra_client_id": cfg.client_id,
        # Never return the secret; only whether one is stored.
        "entra_client_secret_configured": sso_config.secret_configured(db),
        # Read-only: derived from the public base URL. Register this on Entra.
        "entra_redirect_uri": cfg.redirect_uri,
        "link_local_by_email": sso_config.link_local_enabled(db),
        "enabled": cfg.enabled,
    }


@router.get("/sso")
def get_sso(db: Session = Depends(get_db)):
    return _sso_response(db)


class SsoConfigIn(BaseModel):
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    # Write-only: omit/None keeps the stored secret, "" clears it, else replaces.
    entra_client_secret: str | None = None
    # None leaves the toggle unchanged.
    link_local_by_email: bool | None = None


@router.put("/sso")
def update_sso(
    payload: SsoConfigIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    sso_config.save(
        db,
        tenant_id=payload.entra_tenant_id,
        client_id=payload.entra_client_id,
        client_secret=payload.entra_client_secret,
        link_local_by_email=payload.link_local_by_email,
    )
    db.flush()
    # SSO connection edits are the #1 thing to see when debugging a login
    # incident, so make sure they land in both the audit trail and Logs.
    audit_record(db, me, "settings.sso.update", "settings", "sso", {
        "tenant_id": payload.entra_tenant_id,
        "client_id": payload.entra_client_id,
        "secret_changed": payload.entra_client_secret is not None,
        "link_local_by_email": payload.link_local_by_email,
    })
    return _sso_response(db)


@router.get("/mcp-env")
def get_mcp_env(db: Session = Depends(get_db)):
    return {"env_vars": [ev.to_dict() for ev in global_env.list_globals(db)]}


class EnvVarIn(BaseModel):
    name: str = Field(min_length=1)
    value: str = ""


class McpEnvIn(BaseModel):
    env_vars: list[EnvVarIn] = []


@router.put("/mcp-env")
def put_mcp_env(
    payload: McpEnvIn,
    me: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    vars_: list[EnvVar] = []
    for item in payload.env_vars:
        ev = EnvVar.from_dict(item.model_dump())
        if ev is not None:
            vars_.append(ev)
    global_env.save_globals(db, vars_)
    # Flush so reapply_runtime_env reads what we just wrote.
    db.flush()
    get_server_service().reapply_runtime_env_for_all_servers(db)
    audit_record(db, me, "settings.mcp_env.update", "settings", "mcp_env", {
        "names": [v.name for v in vars_],
    })
    return {"env_vars": [v.to_dict() for v in global_env.list_globals(db)]}
