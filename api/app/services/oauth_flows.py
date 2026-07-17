"""Authorization codes and rotating refresh tokens (the stateful half of the
AS; access tokens themselves are stateless JWTs).

Storage discipline matches personal access tokens: the credential string
leaves the process exactly once (in the redirect / token response) and only
its sha256 is persisted.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OAuthAuthCode, OAuthRefreshToken
from app.services.oauth_tokens import new_opaque_token

_CODE_TTL = timedelta(seconds=120)  # one browser redirect; anything longer is a leak window


class FlowError(ValueError):
    """Grant validation failure -> invalid_grant."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes for timezone=True columns; normalise.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ---------- authorization codes ----------


def create_code(
    db: Session,
    *,
    client_id: str,
    user_id: str,
    resource: str,
    scopes: list[str],
    code_challenge: str,
    code_challenge_method: str,
    redirect_uri: str,
) -> str:
    code = new_opaque_token("rhac_")
    db.add(
        OAuthAuthCode(
            code_hash=_sha256(code),
            client_id=client_id,
            user_id=user_id,
            resource=resource,
            scopes=scopes,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            redirect_uri=redirect_uri,
            expires_at=_now() + _CODE_TTL,
        )
    )
    db.flush()
    return code


def _pkce_ok(verifier: str, challenge: str, method: str) -> bool:
    import base64

    if method != "S256":
        return False  # OAuth 2.1: plain is dead
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    derived = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(derived, challenge)


def redeem_code(
    db: Session,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
) -> OAuthAuthCode:
    """Single-use redemption with PKCE. Every check failure is the same
    invalid_grant to the caller; the distinction lives in our logs."""
    row = (
        db.query(OAuthAuthCode)
        .filter(OAuthAuthCode.code_hash == _sha256(code))
        .first()
    )
    if row is None:
        raise FlowError("unknown code")
    if row.consumed_at is not None:
        raise FlowError("code already redeemed")  # replay — attack signal
    if _aware(row.expires_at) < _now():
        raise FlowError("code expired")
    if row.client_id != client_id:
        raise FlowError("code was issued to a different client")
    if row.redirect_uri != redirect_uri:
        raise FlowError("redirect_uri mismatch")
    if not code_verifier or not _pkce_ok(code_verifier, row.code_challenge,
                                         row.code_challenge_method):
        raise FlowError("PKCE verification failed")
    row.consumed_at = _now()
    return row


# ---------- refresh tokens ----------


@dataclass
class RefreshResult:
    row: OAuthRefreshToken
    token: str  # plaintext, response-only


def issue_refresh(
    db: Session,
    *,
    client_id: str,
    user_id: str,
    resource: str,
    scopes: list[str],
) -> RefreshResult:
    token = new_opaque_token("rhrt_")
    days = get_settings().oauth_refresh_token_ttl_days
    row = OAuthRefreshToken(
        token_hash=_sha256(token),
        client_id=client_id,
        user_id=user_id,
        resource=resource,
        scopes=scopes,
        expires_at=_now() + timedelta(days=max(1, int(days))),
    )
    db.add(row)
    db.flush()
    return RefreshResult(row=row, token=token)


def rotate_refresh(db: Session, *, token: str, client_id: str) -> RefreshResult:
    """OAuth 2.1 rotation: every use retires the presented token and issues a
    successor. Presenting an already-rotated token means the string exists in
    two places — treat it as theft and revoke the whole (user, client) family."""
    row = (
        db.query(OAuthRefreshToken)
        .filter(OAuthRefreshToken.token_hash == _sha256(token))
        .first()
    )
    if row is None:
        raise FlowError("unknown refresh token")
    if row.client_id != client_id:
        raise FlowError("refresh token was issued to a different client")
    if row.revoked_at is not None:
        _revoke_family(db, user_id=row.user_id, client_id=row.client_id)
        raise FlowError("refresh token reuse detected; all sessions revoked")
    if _aware(row.expires_at) < _now():
        raise FlowError("refresh token expired")

    fresh = issue_refresh(
        db,
        client_id=row.client_id,
        user_id=row.user_id,
        resource=row.resource,
        scopes=list(row.scopes or []),
    )
    row.revoked_at = _now()
    row.replaced_by = fresh.row.id
    return fresh


def revoke_refresh(db: Session, *, token: str, client_id: str) -> bool:
    """RFC 7009 revocation. Always 'succeeds' per spec; bool is for our logs."""
    row = (
        db.query(OAuthRefreshToken)
        .filter(OAuthRefreshToken.token_hash == _sha256(token))
        .first()
    )
    if row is None or row.client_id != client_id or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    return True


def _revoke_family(db: Session, *, user_id: str, client_id: str) -> None:
    db.query(OAuthRefreshToken).filter(
        OAuthRefreshToken.user_id == user_id,
        OAuthRefreshToken.client_id == client_id,
        OAuthRefreshToken.revoked_at.is_(None),
    ).update({OAuthRefreshToken.revoked_at: _now()})
