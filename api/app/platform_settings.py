"""Helpers around the platform_settings key/value table."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PlatformSetting


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(PlatformSetting, key)
    return row.value if row is not None else default


def put_setting(db: Session, key: str, value: str) -> None:
    row = db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value))
    else:
        row.value = value


def forget_setting(db: Session, key: str) -> None:
    row = db.get(PlatformSetting, key)
    if row is not None:
        db.delete(row)


# Stable keys (mirror SettingsController PHP constants).
SETTING_HOSTNAME = "hostname"
SETTING_EXTERNAL_HTTPS = "external_https"
SETTING_DOCKER_REGISTRY = "docker_registry"
SETTING_DOCKER_REGISTRY_USERNAME = "docker_registry_username"
SETTING_DOCKER_REGISTRY_PASSWORD = "docker_registry_password"
SETTING_CUSTOM_CA_CERT = "custom_ca_cert"
SETTING_GLOBAL_ENV_VARS = "mcp_global_env_vars"

# Self-managed TLS (see app.services.tls_cert). The cert chain is public and
# stored plain; the private key is encrypted at rest with the app.crypto AES
# envelope (keyed off APP_KEY), like the Entra client secret. These are the
# source of truth; the live Docker secrets on the Traefik service are derived
# from them and can be re-synced from here.
SETTING_TLS_CERT = "tls_cert"
SETTING_TLS_KEY = "tls_key"

# Entra ID SSO (OIDC) connection — configured in the UI, stored here (not env).
# The client secret is encrypted at rest with the app.crypto AES envelope. The
# redirect URI is NOT stored: it's derived from the public base URL.
SETTING_ENTRA_TENANT_ID = "entra_tenant_id"
SETTING_ENTRA_CLIENT_ID = "entra_client_id"
SETTING_ENTRA_CLIENT_SECRET = "entra_client_secret"
# When "true", a first SSO login whose email matches an existing LOCAL account
# links to it (adopts the user) instead of refusing. Opt-in; default off.
SETTING_SSO_LINK_LOCAL = "sso_link_local_by_email"
