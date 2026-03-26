from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_superadmin,
    verify_password,
)
from app.database import get_db
from app.db_models import User
from app.models import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter()


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token, user=_user_response(user))


@router.get("/auth/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.post("/auth/register", response_model=UserResponse, status_code=201)
def register(
    req: RegisterRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        display_name=req.display_name,
        role=req.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_response(user)
