"""Bearer token auth.

Token wire format: `{id}|{plaintext}` — split on `|`, look up the row by
`id`, then compare sha256(plaintext) to the stored `token` column."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models import PersonalAccessToken, User
from app.config import get_settings


# ---------- Passwords ----------

def hash_password(plaintext: str) -> str:
    # Cost 12. Both $2y$ and $2b$ prefixes are accepted on verify by the
    # underlying bcrypt library, so existing hashes with either prefix work.
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    if not hashed:
        return False
    # bcrypt.checkpw understands both $2y$ and $2b$ prefixes.
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- Personal access tokens ----------

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def issue_token(db: Session, user: User, name: str = "api") -> str:
    """Create a new personal access token and return the plaintext form the
    client must store. The DB stores only sha256(plaintext)."""
    raw = secrets.token_hex(20)  # 40 hex chars
    row = PersonalAccessToken(
        tokenable_type="App\\Models\\User",
        tokenable_id=str(user.id),
        name=name,
        token=_sha256(raw),
        abilities='["*"]',
    )
    db.add(row)
    db.flush()
    return f"{row.id}|{raw}"


def parse_bearer(header_value: str | None) -> tuple[int, str] | None:
    """Parse `Authorization: Bearer {id}|{raw}` → (id, raw). Returns None on
    any parse failure (including missing header / wrong scheme)."""
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token_part = parts[1].strip()
    if "|" not in token_part:
        return None
    id_str, _, raw = token_part.partition("|")
    try:
        return int(id_str), raw
    except ValueError:
        return None


def resolve_token(db: Session, header_value: str | None) -> User | None:
    parsed = parse_bearer(header_value)
    if not parsed:
        return None
    token_id, raw = parsed
    row = db.get(PersonalAccessToken, token_id)
    if row is None:
        return None
    if not hmac.compare_digest(row.token, _sha256(raw)):
        return None
    if row.expires_at is not None and row.expires_at < datetime.now(timezone.utc):
        return None
    expiry_minutes = get_settings().auth_token_expiration_minutes
    if expiry_minutes and row.created_at:
        # When expires_at is null, fall back to a TTL measured from created_at.
        expires = row.created_at + timedelta(minutes=expiry_minutes)
        if expires < datetime.now(timezone.utc):
            return None
    row.last_used_at = datetime.now(timezone.utc)
    user = db.get(User, row.tokenable_id)
    return user
