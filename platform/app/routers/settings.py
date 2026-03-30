from __future__ import annotations

import logging
import yaml
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.auth import require_superadmin
from app.config import TRAEFIK_CERTS_DIR, TRAEFIK_DYNAMIC_DIR
from app.database import get_db
from app.db_models import PlatformSetting, User

logger = logging.getLogger(__name__)
router = APIRouter()

SETTING_HOSTNAME = "hostname"
SETTING_TLS_ENABLED = "tls_enabled"


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


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    cert_path = TRAEFIK_CERTS_DIR / "cert.pem"
    return {
        "hostname": _get_setting(db, SETTING_HOSTNAME),
        "tls_enabled": _get_setting(db, SETTING_TLS_ENABLED) == "true",
        "has_certificate": cert_path.exists(),
        "base_url": get_base_url(db),
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
