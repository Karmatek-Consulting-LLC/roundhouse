from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import record as audit_record
from app.db import get_db
from app.deps import current_user
from app.models import ServerScope, User
from app.services import permissions, server_auth
from app.services.store import ServerStore

router = APIRouter(prefix="/api/servers", tags=["server-scopes"])

_SCOPE_NAME_RE = re.compile(r"^[a-zA-Z0-9_:.\-]+$")


def _assert_access(db: Session, user: User, name: str) -> None:
    if not permissions.can_access(db, user, name):
        raise HTTPException(status_code=403, detail="Access denied")


def _scope_to_api(s: ServerScope) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.get("/{name}/scopes")
def index(
    name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    scopes = (
        db.query(ServerScope)
        .filter(ServerScope.server_name == name)
        .order_by(ServerScope.name)
        .all()
    )
    return [_scope_to_api(s) for s in scopes]


class ScopeIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


@router.post("/{name}/scopes", status_code=201)
def store(
    name: str,
    payload: ScopeIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    if not _SCOPE_NAME_RE.match(payload.name):
        raise HTTPException(status_code=422, detail="Invalid scope name")
    exists = (
        db.query(ServerScope)
        .filter(ServerScope.server_name == name, ServerScope.name == payload.name)
        .first()
    )
    if exists:
        raise HTTPException(status_code=422, detail="Scope already exists")
    scope = server_auth.create_scope(db, name, payload.name, payload.description)
    audit_record(db, user, "scope.create", "server_scope", f"{name}/{payload.name}")
    return _scope_to_api(scope)


class ScopeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


@router.put("/{name}/scopes/{scope_name}")
def update(
    name: str,
    scope_name: str,
    payload: ScopeUpdate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    scope = (
        db.query(ServerScope)
        .filter(ServerScope.server_name == name, ServerScope.name == scope_name)
        .first()
    )
    if not scope:
        raise HTTPException(status_code=404, detail=f"Scope '{scope_name}' not found.")

    store = ServerStore()
    if payload.name and payload.name != scope.name:
        if not _SCOPE_NAME_RE.match(payload.name):
            raise HTTPException(status_code=422, detail="Invalid scope name")
        clash = (
            db.query(ServerScope)
            .filter(ServerScope.server_name == name, ServerScope.name == payload.name)
            .first()
        )
        if clash:
            raise HTTPException(status_code=422, detail="Scope already exists")
        server_auth.rename_scope(db, store, name, scope.name, payload.name)
        scope = (
            db.query(ServerScope)
            .filter(ServerScope.server_name == name, ServerScope.name == payload.name)
            .first()
        )
    if payload.description is not None and scope is not None:
        scope.description = payload.description
        server_auth.mark_redeploy_required(db, name)
    audit_record(db, user, "scope.update", "server_scope", f"{name}/{scope_name}", {
        "renamed_to": payload.name if payload.name and payload.name != scope_name else None,
    })
    return _scope_to_api(scope)


@router.delete("/{name}/scopes/{scope_name}", status_code=status.HTTP_204_NO_CONTENT)
def destroy(
    name: str,
    scope_name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _assert_access(db, user, name)
    exists = (
        db.query(ServerScope)
        .filter(ServerScope.server_name == name, ServerScope.name == scope_name)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Scope '{scope_name}' not found.")
    server_auth.delete_scope(db, ServerStore(), name, scope_name)
    audit_record(db, user, "scope.delete", "server_scope", f"{name}/{scope_name}")
