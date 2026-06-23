"""Admin CRUD for the Entra app role -> Roundhouse grant mapping table.

Superadmin-only. These rows drive the claim->grant engine on every SSO login,
so they are the lever an admin uses to grant/revoke access for SSO users. See
docs/entra-sso-plan.md §3.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_superadmin
from app.models import RoleMapping, Team

router = APIRouter(
    prefix="/api/role-mappings",
    tags=["auth"],
    dependencies=[Depends(require_superadmin)],
)


class RoleMappingIn(BaseModel):
    entra_app_role: str = Field(min_length=1, max_length=255)
    roundhouse_role: Literal["superadmin", "user"]
    team_id: str | None = None
    team_role: Literal["admin", "member"] = "member"


def _to_api(m: RoleMapping) -> dict:
    return {
        "id": m.id,
        "entra_app_role": m.entra_app_role,
        "roundhouse_role": m.roundhouse_role,
        "team_id": str(m.team_id) if m.team_id else None,
        "team_role": m.team_role,
    }


def _validate_team(db: Session, team_id: str | None) -> None:
    if team_id and db.get(Team, team_id) is None:
        raise HTTPException(status_code=404, detail="Team not found")


def _find(db: Session, mapping_id: int) -> RoleMapping:
    m = db.get(RoleMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return m


@router.get("")
def index(db: Session = Depends(get_db)):
    rows = db.query(RoleMapping).order_by(RoleMapping.entra_app_role).all()
    return [_to_api(m) for m in rows]


@router.post("", status_code=201)
def store(payload: RoleMappingIn, db: Session = Depends(get_db)):
    if db.query(RoleMapping).filter(RoleMapping.entra_app_role == payload.entra_app_role).first():
        raise HTTPException(status_code=409, detail="A mapping for this app role already exists")
    _validate_team(db, payload.team_id)
    m = RoleMapping(
        entra_app_role=payload.entra_app_role,
        roundhouse_role=payload.roundhouse_role,
        team_id=payload.team_id,
        team_role=payload.team_role,
    )
    db.add(m)
    db.flush()
    return _to_api(m)


@router.put("/{mapping_id}")
def update(mapping_id: int, payload: RoleMappingIn, db: Session = Depends(get_db)):
    m = _find(db, mapping_id)
    clash = (
        db.query(RoleMapping)
        .filter(
            RoleMapping.entra_app_role == payload.entra_app_role,
            RoleMapping.id != mapping_id,
        )
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="A mapping for this app role already exists")
    _validate_team(db, payload.team_id)
    m.entra_app_role = payload.entra_app_role
    m.roundhouse_role = payload.roundhouse_role
    m.team_id = payload.team_id
    m.team_role = payload.team_role
    db.flush()
    return _to_api(m)


@router.delete("/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def destroy(mapping_id: int, db: Session = Depends(get_db)):
    db.delete(_find(db, mapping_id))
