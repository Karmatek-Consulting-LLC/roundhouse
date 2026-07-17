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

# Base image registry used to PULL the generated MCP-server base images at build
# time (e.g. dhi.io for Docker Hardened Images). Distinct from the docker
# registry above, which is where BUILT server images are PUSHED. The password is
# encrypted at rest with the app.crypto AES envelope (keyed off APP_KEY), like
# the Entra client secret. Delivered to the build daemon as X-Registry-Config so
# the authenticated `FROM` pull succeeds.
SETTING_BASE_REGISTRY = "base_registry"
SETTING_BASE_REGISTRY_USERNAME = "base_registry_username"
SETTING_BASE_REGISTRY_PASSWORD = "base_registry_password"

# Base images for generated MCP-server builds, overriding the MCP_SERVER_*_IMAGE
# env defaults when set. Two stages: the build image (root; ships pip + apt) and
# the runtime image (non-root, distroless). Stored plain — they're image refs,
# not secrets.
SETTING_MCP_BASE_BUILD_IMAGE = "mcp_base_build_image"
SETTING_MCP_BASE_RUNTIME_IMAGE = "mcp_base_runtime_image"

# Registry vulnerability scanning. When set to "harbor", the platform reads
# Trivy scan overviews from the registry's REST API (using the registry
# credentials above) and surfaces per-server vulnerability badges in the UI.
# The API base defaults to https://{registry-host}/api/v2.0; the override
# exists for registries whose API is reached on a different URL.
SETTING_REGISTRY_SCANNER = "registry_scanner"
SETTING_REGISTRY_SCANNER_API_URL = "registry_scanner_api_url"

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

# OAuth 2.1 authorization server (Phase 2, MCP data path). The signing key is
# an RSA private key in JWK JSON form, encrypted at rest with the app.crypto
# AES envelope (keyed off APP_KEY) like the Entra client secret; the kid is
# stored plain so /jwks.json can be served without decrypting. DCR defaults on
# ("" == unset -> enabled); set "false" to close anonymous registration.
# Assertion profiles is a JSON list configuring the jwt-bearer grant — see
# app.services.oauth_assertions (docs/mcp-auth-id-jag.md §7 "pluggable
# assertion profile").
SETTING_OAUTH_SIGNING_KEY = "oauth_signing_key"
SETTING_OAUTH_SIGNING_KID = "oauth_signing_kid"
SETTING_OAUTH_DCR_ENABLED = "oauth_dcr_enabled"
SETTING_OAUTH_ASSERTION_PROFILES = "oauth_assertion_profiles"

# Per-context retention for the Logs console, days as a string (e.g.
# "log_retention.auth" = "90"). "0" keeps forever. Unset falls back to the
# RH_LOG_RETENTION_DAYS env var (default 90). See app.services.log_retention.
SETTING_LOG_RETENTION_PREFIX = "log_retention."
