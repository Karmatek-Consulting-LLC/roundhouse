"""Signing keys for the Roundhouse authorization server.

One RSA keypair, generated on first use and persisted in platform_settings:
the private key (JWK JSON) encrypted with the app.crypto AES envelope (keyed
off APP_KEY, like the Entra client secret and TLS private key), the kid plain.
Spawned MCP servers never see this key — they fetch the public half from
/.well-known/jwks.json and validate statelessly (docs/mcp-auth-id-jag.md §9).

Rotation: `rotate_signing_key` mints a new keypair and *keeps the old public
key in the JWKS* so outstanding access tokens (TTL <= 1h) still validate;
the retired public key ages out on the next rotation.
"""
from __future__ import annotations

import json
import secrets

from authlib.jose import JsonWebKey
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import DecryptError, decrypt, encrypt, looks_encrypted
from app.db import advisory_lock
from app.platform_settings import (
    SETTING_OAUTH_SIGNING_KEY,
    SETTING_OAUTH_SIGNING_KID,
    get_setting,
    put_setting,
)

# Previous public keys kept in the served JWKS across a rotation (plain JSON —
# public material only).
_SETTING_RETIRED_JWKS = "oauth_retired_jwks"
# Advisory-lock key for first-use generation (multi-worker safe).
_KEYGEN_LOCK = 0x52480A01


class OAuthKeyError(RuntimeError):
    """Signing key unavailable/undecryptable. Callers map to a 500."""


def _new_kid() -> str:
    return "rh-" + secrets.token_hex(6)


def _decrypt_stored(stored: str) -> str:
    if not looks_encrypted(stored):
        return stored  # dev-mode plaintext fallback (no APP_KEY), same as sso_config
    try:
        return decrypt(stored, get_settings().app_key)
    except DecryptError as e:
        raise OAuthKeyError(
            "OAuth signing key cannot be decrypted (APP_KEY changed?)"
        ) from e


def _generate(db: Session) -> None:
    kid = _new_kid()
    key = JsonWebKey.generate_key("RSA", 2048, {"kid": kid, "use": "sig", "alg": "RS256"},
                                  is_private=True)
    private_json = json.dumps(key.as_dict(is_private=True))
    app_key = get_settings().app_key
    stored = encrypt(private_json, app_key) if app_key else private_json
    put_setting(db, SETTING_OAUTH_SIGNING_KEY, stored)
    put_setting(db, SETTING_OAUTH_SIGNING_KID, kid)
    # Sessions run autoflush=False; make the rows visible to the read-back
    # (db.get) that immediately follows.
    db.flush()


def ensure_signing_key(db: Session) -> None:
    """Generate the keypair on first use. Advisory-locked so concurrent workers
    don't race to write two different keys."""
    if get_setting(db, SETTING_OAUTH_SIGNING_KEY):
        return
    with advisory_lock(_KEYGEN_LOCK):
        # Re-check under the lock: another worker may have generated meanwhile.
        # The setting row is not in this session's identity map (the pre-lock
        # read returned None), so db.get issues a real SELECT and — under READ
        # COMMITTED — sees the other worker's committed write. Deliberately NOT
        # db.expire_all(): expiring discards the caller's unflushed changes
        # (e.g. an auth code's consumed_at) and this runs mid-request.
        if get_setting(db, SETTING_OAUTH_SIGNING_KEY):
            return
        _generate(db)


def signing_key(db: Session) -> JsonWebKey:
    """The private signing key (generating it on first use)."""
    ensure_signing_key(db)
    stored = get_setting(db, SETTING_OAUTH_SIGNING_KEY) or ""
    if not stored:
        raise OAuthKeyError("OAuth signing key missing after generation")
    return JsonWebKey.import_key(json.loads(_decrypt_stored(stored)))


def signing_kid(db: Session) -> str:
    ensure_signing_key(db)
    return get_setting(db, SETTING_OAUTH_SIGNING_KID) or ""


def _public_dict(key: JsonWebKey) -> dict:
    # authlib drops use/alg from the public projection; put them back so
    # strict JWKS consumers can filter on them.
    return {**key.as_dict(is_private=False), "use": "sig", "alg": "RS256"}


def public_jwks(db: Session) -> dict:
    """The served JWKS: current public key + any retired-but-unexpired one."""
    key = signing_key(db)
    keys = [_public_dict(key)]
    retired = get_setting(db, _SETTING_RETIRED_JWKS)
    if retired:
        try:
            keys.extend(json.loads(retired))
        except ValueError:
            pass
    return {"keys": keys}


def rotate_signing_key(db: Session) -> str:
    """Mint a new keypair; keep the outgoing public key served so tokens
    signed with it validate until they expire. Returns the new kid."""
    old = signing_key(db)
    put_setting(db, _SETTING_RETIRED_JWKS, json.dumps([_public_dict(old)]))
    _generate(db)
    return signing_kid(db)
