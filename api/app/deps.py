"""FastAPI dependencies: DB session, current user, role gates."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import resolve_token
from app.db import get_db
from app.models import User


def current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    user = resolve_token(db, authorization)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_superadmin(user: User = Depends(current_user)) -> User:
    if not user.is_superadmin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user
