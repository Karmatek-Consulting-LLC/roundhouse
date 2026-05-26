from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.db import get_db
from app.deps import current_user, require_superadmin
from app.models import User

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_superadmin)])


class SetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


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
