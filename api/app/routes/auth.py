from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import hash_password, issue_token, verify_password
from app.db import get_db
from app.deps import current_user, require_superadmin
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
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = issue_token(db, user, name="api")
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user.to_api(),
    }


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user.to_api()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from your current password",
        )
    user.password_hash = hash_password(payload.new_password)
    db.add(user)


@router.post("/register", status_code=201)
def register(
    payload: RegisterIn,
    _: User = Depends(require_superadmin),
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
    return user.to_api()
