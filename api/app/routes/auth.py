from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import logbook
from app.auth import hash_password, issue_token, verify_password
from app.db import get_db
from app.deps import current_user, require_superadmin
from app.logbook import CONTEXT_AUTH
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    # Don't gate on EmailStr - admin@mcp.local and other .local addresses are
    # legitimate in this product's deployments.
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=256)


class RegisterIn(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)
    role: str = Field(default="user")


@router.post("/login")
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        # The reason stays server-side (the client only sees the generic 401)
        # but is gold when troubleshooting from the Logs console: an SSO-only
        # account trying password login looks identical to a bad password
        # from the user's side.
        if not user:
            reason = "unknown_email"
        elif not user.password_hash:
            reason = "sso_only_account"
        else:
            reason = "bad_password"
        logbook.record(
            CONTEXT_AUTH, "login", logbook.OUTCOME_FAILURE,
            request=request, email=payload.email,
            message="Invalid email or password",
            detail={"method": "password", "reason": reason},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = issue_token(db, user, name="api")
    logbook.record(
        CONTEXT_AUTH, "login", logbook.OUTCOME_SUCCESS,
        request=request, user=user,
        message="Signed in with password",
        detail={"method": "password"},
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user.to_api(),
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, user: User = Depends(current_user)):
    """Best-effort logout marker. The session token itself is client-held
    (the SPA clears localStorage); this exists so sign-outs show up in the
    auth log alongside sign-ins."""
    logbook.record(
        CONTEXT_AUTH, "logout", logbook.OUTCOME_SUCCESS,
        request=request, user=user, message="Signed out",
    )


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user.to_api()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        logbook.record(
            CONTEXT_AUTH, "password.change", logbook.OUTCOME_FAILURE,
            request=request, user=user,
            message="Current password is incorrect",
        )
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from your current password",
        )
    user.password_hash = hash_password(payload.new_password)
    db.add(user)
    logbook.record(
        CONTEXT_AUTH, "password.change", logbook.OUTCOME_SUCCESS,
        request=request, user=user, message="Password changed",
    )


@router.post("/register", status_code=201)
def register(
    payload: RegisterIn,
    request: Request,
    admin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role=payload.role or "user",
    )
    db.add(user)
    db.flush()
    logbook.record(
        CONTEXT_AUTH, "user.register", logbook.OUTCOME_SUCCESS,
        request=request, user=admin,
        message=f"Registered user {user.email}",
        detail={"email": user.email, "role": user.role},
    )
    return user.to_api()
