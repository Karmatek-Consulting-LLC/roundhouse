from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.auth import hash_password
from app.db import get_db
from app.deps import current_user, require_superadmin
from app.models import User
from app.services.sso import would_orphan_last_superadmin

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_superadmin)])


class SetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class UpdateUserIn(BaseModel):
    """Partial update of a user's role and/or auth source. Both optional; only
    the supplied fields change. Literal types reject bad values with a 422."""

    role: Literal["user", "superadmin"] | None = None
    auth_source: Literal["local", "entra"] | None = None


def _find_or_404(db: Session, user_id: str) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.get("")
def index(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.email).all()
    return [u.to_api() for u in users]


@router.get("/{user_id}")
def show(user_id: str, db: Session = Depends(get_db)):
    return _find_or_404(db, user_id).to_api()


@router.patch("/{user_id}")
def update(
    user_id: str,
    payload: UpdateUserIn,
    me: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Admin-edit a user's role and/or auth source.

    Converting an Entra (SSO) user to "local" is a break-glass move: it exempts
    them from SSO role sync so an admin can set a password and manage the role
    directly — for when Entra is down/misconfigured or hasn't granted the mapped
    role yet. The oidc_sub link is deliberately KEPT, so once Entra is healthy
    the admin flips them back to "entra" and SSO resumes seamlessly (the next
    login matches by oidc_sub and re-syncs the role from claims). Demoting the
    last superadmin is rejected, mirroring the SSO-sync floor."""
    user = _find_or_404(db, user_id)
    changes: dict = {}

    if payload.auth_source is not None and payload.auth_source != user.auth_source:
        # Keep oidc_sub across the toggle: "local" is a temporary break-glass
        # state, and retaining the subject lets the user fall straight back onto
        # SSO when flipped to "entra" without an admin having to re-link them.
        changes["auth_source"] = {"from": user.auth_source, "to": payload.auth_source}
        user.auth_source = payload.auth_source

    if payload.role is not None and payload.role != user.role:
        if would_orphan_last_superadmin(db, user, payload.role):
            raise HTTPException(status_code=400, detail="Cannot demote the last superadmin")
        changes["role"] = {"from": user.role, "to": payload.role}
        user.role = payload.role

    if changes:
        audit_record(db, me, "user.update", "user", user_id, changes)
    return user.to_api()


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def set_password(user_id: str, payload: SetPasswordIn, db: Session = Depends(get_db)):
    user = _find_or_404(db, user_id)
    user.password_hash = hash_password(payload.new_password)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def destroy(
    user_id: str,
    me: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if str(me.id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = _find_or_404(db, user_id)
    db.delete(user)
