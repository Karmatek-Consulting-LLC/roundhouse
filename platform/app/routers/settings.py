from __future__ import annotations

import logging
import yaml
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_superadmin
from app.docker_manager import DockerManager
from app.config import (
    DEFAULT_MCP_SERVER_REPLICAS,
    MAX_MCP_SERVER_REPLICAS,
    TRAEFIK_CERTS_DIR,
    TRAEFIK_DYNAMIC_DIR,
)
from app.database import get_db
from app.db_models import PlatformSetting, User
from app.mcp_env import (
    all_registered_server_names,
    global_env_vars_from_db,
    reapply_runtime_env_for_servers,
    save_global_env_vars,
)
from app.models import UpdateEnvVarsRequest
from app.server_store import ServerStore

logger = logging.getLogger(__name__)
router = APIRouter()

SETTING_HOSTNAME = "hostname"
SETTING_TLS_ENABLED = "tls_enabled"
SETTING_DOCKER_REGISTRY = "docker_registry"
SETTING_DOCKER_REGISTRY_USERNAME = "docker_registry_username"
SETTING_DOCKER_REGISTRY_PASSWORD = "docker_registry_password"


def _get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    return row.value if row else default


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(PlatformSetting).filter(PlatformSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(PlatformSetting(key=key, value=value))
    db.commit()


def get_docker_registry_setting(db: Session) -> str:
    """Raw value from platform settings (may be empty)."""
    return _get_setting(db, SETTING_DOCKER_REGISTRY).strip()


def get_docker_registry_prefix(db: Session) -> str | None:
    """Registry prefix from platform settings only; empty means local images on the build host."""
    raw = get_docker_registry_setting(db)
    if not raw:
        return None
    return raw.rstrip("/")


def get_docker_registry_auth(db: Session) -> dict[str, str] | None:
    """Credentials for ``docker push`` when a registry prefix is set (Harbor robot user, etc.)."""
    if not get_docker_registry_prefix(db):
        return None
    username = _get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME).strip()
    password = (_get_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD) or "").strip()
    if not username or not password:
        return None
    return {"username": username, "password": password}


def docker_registry_password_configured(db: Session) -> bool:
    """True if a push password is stored in platform settings."""
    return bool(_get_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD).strip())


def get_base_url(db: Session) -> str:
    """Get the current base URL from settings, with fallback to env."""
    from app.config import MCP_BASE_URL
    hostname = _get_setting(db, SETTING_HOSTNAME)
    if not hostname:
        return MCP_BASE_URL
    tls = _get_setting(db, SETTING_TLS_ENABLED) == "true"
    scheme = "https" if tls else "http"
    return f"{scheme}://{hostname}"


def _write_traefik_tls_config() -> None:
    """Write Traefik dynamic config for TLS."""
    cert_path = TRAEFIK_CERTS_DIR / "cert.pem"
    key_path = TRAEFIK_CERTS_DIR / "key.pem"

    if not cert_path.exists() or not key_path.exists():
        # Remove dynamic config if certs don't exist
        config_path = TRAEFIK_DYNAMIC_DIR / "tls.yml"
        if config_path.exists():
            config_path.unlink()
        return

    config = {
        "tls": {
            "stores": {
                "default": {
                    "defaultCertificate": {
                        "certFile": "/etc/traefik/certs/cert.pem",
                        "keyFile": "/etc/traefik/certs/key.pem",
                    }
                }
            }
        }
    }

    TRAEFIK_DYNAMIC_DIR.mkdir(parents=True, exist_ok=True)
    config_path = TRAEFIK_DYNAMIC_DIR / "tls.yml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    logger.info("Wrote Traefik TLS config to %s", config_path)


@router.get("/settings/mcp-env")
def get_mcp_env_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    return {"env_vars": global_env_vars_from_db(db)}


@router.put("/settings/mcp-env")
def put_mcp_env_settings(
    body: UpdateEnvVarsRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    save_global_env_vars(db, body.env_vars)
    store = ServerStore()
    reapply_runtime_env_for_servers(
        db, all_registered_server_names(db), DockerManager(), store
    )
    return {"env_vars": global_env_vars_from_db(db)}


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    cert_path = TRAEFIK_CERTS_DIR / "cert.pem"
    swarm = DockerManager().swarm_mode
    return {
        "hostname": _get_setting(db, SETTING_HOSTNAME),
        "tls_enabled": _get_setting(db, SETTING_TLS_ENABLED) == "true",
        "has_certificate": cert_path.exists(),
        "base_url": get_base_url(db),
        "default_mcp_server_replicas": DEFAULT_MCP_SERVER_REPLICAS,
        "max_mcp_server_replicas": MAX_MCP_SERVER_REPLICAS,
        "docker_swarm_mode": swarm,
        "docker_registry": get_docker_registry_setting(db),
        "docker_registry_effective": get_docker_registry_prefix(db) or "",
        "docker_registry_username": _get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME),
        "docker_registry_password_configured": docker_registry_password_configured(db),
    }


class DockerRegistryBody(BaseModel):
    """Registry prefix and optional push credentials (Harbor, Docker Hub, etc.)."""

    registry: str = ""
    username: str = ""
    password: str | None = None  # omit = leave unchanged; "" = clear stored password


@router.put("/settings/docker-registry")
def update_docker_registry(
    body: DockerRegistryBody,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    value = body.registry.strip()
    _set_setting(db, SETTING_DOCKER_REGISTRY, value)
    _set_setting(db, SETTING_DOCKER_REGISTRY_USERNAME, body.username.strip())
    if "password" in body.model_fields_set:
        _set_setting(db, SETTING_DOCKER_REGISTRY_PASSWORD, body.password or "")
    eff = get_docker_registry_prefix(db)
    return {
        "docker_registry": value,
        "docker_registry_effective": eff or "",
        "docker_registry_username": _get_setting(db, SETTING_DOCKER_REGISTRY_USERNAME),
        "docker_registry_password_configured": docker_registry_password_configured(db),
    }


@router.put("/settings/hostname")
def update_hostname(
    hostname: str = Form(...),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    _set_setting(db, SETTING_HOSTNAME, hostname.strip())
    return {"hostname": hostname.strip(), "base_url": get_base_url(db)}


@router.post("/settings/certificate")
def upload_certificate(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    TRAEFIK_CERTS_DIR.mkdir(parents=True, exist_ok=True)

    cert_content = cert.file.read()
    key_content = key.file.read()

    # Basic validation
    if b"BEGIN CERTIFICATE" not in cert_content:
        raise HTTPException(status_code=400, detail="Invalid certificate file (expected PEM format)")
    if b"PRIVATE KEY" not in key_content:
        raise HTTPException(status_code=400, detail="Invalid key file (expected PEM format)")

    (TRAEFIK_CERTS_DIR / "cert.pem").write_bytes(cert_content)
    (TRAEFIK_CERTS_DIR / "key.pem").write_bytes(key_content)

    _set_setting(db, SETTING_TLS_ENABLED, "true")
    _write_traefik_tls_config()

    return {"tls_enabled": True, "base_url": get_base_url(db)}


@router.delete("/settings/certificate")
def delete_certificate(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    for f in ["cert.pem", "key.pem"]:
        path = TRAEFIK_CERTS_DIR / f
        if path.exists():
            path.unlink()

    _set_setting(db, SETTING_TLS_ENABLED, "false")
    _write_traefik_tls_config()

    return {"tls_enabled": False, "base_url": get_base_url(db)}
