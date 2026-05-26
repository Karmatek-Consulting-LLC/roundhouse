from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.db import get_db
from app.deps import current_user
from app.models import ServerScope, ServerToken, User
from app.services import permissions, server_auth

router = APIRouter(prefix="/api/servers", tags=["server-tokens"])


def _assert_access(db: Session, user: User, name: str) -> None:
    if not permissions.can_access(db, user, name):
        raise HTTPException(status_code=403, detail="Access denied")


def _assert_scopes_exist(db: Session, server: str, scopes: list[str]) -> None:
    if not scopes:
        return
    rows = (
        db.query(ServerScope.name)
        .filter(ServerScope.server_name == server, ServerScope.name.in_(scopes))
        .all()
    )
    known = {n for (n,) in rows}
    unknown = [s for s in scopes if s not in known]
    if unknown:
        raise HTTPException(status_code=422, detail="Unknown scopes: " + ", ".join(unknown))


def _token_to_api(t: ServerToken) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "display_prefix": t.display_prefix,
        "scopes": list(t.scopes or []),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/{name}/tokens")
def index(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    tokens = (
        db.query(ServerToken)
        .filter(ServerToken.server_name == name)
        .order_by(ServerToken.id)
        .all()
    )
    return [_token_to_api(t) for t in tokens]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = []


@router.post("/{name}/tokens", status_code=201)
def store(
    name: str,
    payload: TokenIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    exists = (
        db.query(ServerToken)
        .filter(ServerToken.server_name == name, ServerToken.name == payload.name)
        .first()
    )
    if exists:
        raise HTTPException(status_code=422, detail="Token name already exists")
    _assert_scopes_exist(db, name, payload.scopes)
    row, plaintext = server_auth.mint_token(db, name, payload.name, payload.scopes)
    audit_record(db, user, "token.mint", "server_token", str(row.id), {
        "server_name": name, "name": payload.name, "scopes": payload.scopes,
    })
    return {**_token_to_api(row), "token": plaintext}


@router.delete("/{name}/tokens/{id}", status_code=status.HTTP_204_NO_CONTENT)
def destroy(
    name: str,
    id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    if not server_auth.revoke_token(db, name, id):
        raise HTTPException(status_code=404, detail=f"Token {id} not found.")
    audit_record(db, user, "token.revoke", "server_token", str(id), {"server_name": name})
