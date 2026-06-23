"""Entra ID SSO connection config, stored in platform_settings (UI-managed).

Tenant id, client id, and client secret live in the platform_settings key/value
table rather than environment variables, so an operator configures SSO from the
dashboard. The client secret is encrypted at rest with the same AES envelope
used for server_tokens (keyed off APP_KEY); the rest is stored plain.

The redirect URI is NOT stored — it's derived from the deploy-time public base
URL (MCP_BASE_URL / PUBLIC_HOSTNAME) as `<base>/api/auth/oidc/callback`. The
OIDC callback is hit by the user's browser, which always reaches the app at the
public host, and the dashboard + MCP servers share that one host — so there is
no scenario where the redirect host differs from the base URL. Deriving it keeps
it in lockstep and removes a field that could mismatch the Entra registration.

`load(db)` returns an EntraConfig the OIDC client + routes consume; `enabled` is
true once tenant, client, and secret are present (the redirect is always derived).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import DecryptError, decrypt, encrypt, looks_encrypted
from app.platform_settings import (
    SETTING_ENTRA_CLIENT_ID,
    SETTING_ENTRA_CLIENT_SECRET,
    SETTING_ENTRA_TENANT_ID,
    forget_setting,
    get_setting,
    put_setting,
)

# Path of the OIDC callback route (app-fixed); appended to the public base URL.
_CALLBACK_PATH = "/api/auth/oidc/callback"


def redirect_uri() -> str:
    """The OIDC redirect URI, derived from the deploy-time public base URL."""
    return f"{get_settings().mcp_base_url.rstrip('/')}{_CALLBACK_PATH}"


@dataclass(frozen=True)
class EntraConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def enabled(self) -> bool:
        """SSO is live once the credentials are set (redirect is always derived)."""
        return bool(self.tenant_id and self.client_id and self.client_secret and self.redirect_uri)

    @property
    def discovery_url(self) -> str:
        return (
            f"https://login.microsoftonline.com/{self.tenant_id}"
            "/v2.0/.well-known/openid-configuration"
        )

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


def _decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not looks_encrypted(stored):
        # Plaintext fallback (e.g. written when APP_KEY was unset); use as-is.
        return stored
    try:
        return decrypt(stored, get_settings().app_key)
    except DecryptError:
        return ""


def load(db: Session) -> EntraConfig:
    return EntraConfig(
        tenant_id=(get_setting(db, SETTING_ENTRA_TENANT_ID, "") or "").strip(),
        client_id=(get_setting(db, SETTING_ENTRA_CLIENT_ID, "") or "").strip(),
        client_secret=_decrypt_secret(get_setting(db, SETTING_ENTRA_CLIENT_SECRET, "") or ""),
        redirect_uri=redirect_uri(),
    )


def secret_configured(db: Session) -> bool:
    return bool((get_setting(db, SETTING_ENTRA_CLIENT_SECRET, "") or "").strip())


def save(
    db: Session,
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str | None,
) -> None:
    """Persist connection settings. `client_secret` is write-only: None keeps the
    stored value, "" clears it, any other value replaces it (encrypted)."""
    put_setting(db, SETTING_ENTRA_TENANT_ID, tenant_id.strip())
    put_setting(db, SETTING_ENTRA_CLIENT_ID, client_id.strip())
    if client_secret is not None:
        if client_secret == "":
            forget_setting(db, SETTING_ENTRA_CLIENT_SECRET)
        else:
            app_key = get_settings().app_key
            stored = encrypt(client_secret, app_key) if app_key else client_secret
            put_setting(db, SETTING_ENTRA_CLIENT_SECRET, stored)
