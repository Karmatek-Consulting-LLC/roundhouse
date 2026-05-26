from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user, require_superadmin
from app.models import Team, TeamMembership, User

router = APIRouter(prefix="/api/teams", tags=["teams"])


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class MemberIn(BaseModel):
    user_id: str
    role: Literal["admin", "member"] = "member"


def _find_team(db: Session, team_id: str) -> Team:
    t = db.get(Team, team_id)
    if not t:
        raise HTTPException(status_code=404, detail="Team not found")
    return t


def _assert_can_manage(db: Session, user: User, team: Team) -> None:
    if user.is_superadmin():
        return
    m = (
        db.query(TeamMembership)
        .filter(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == user.id,
            TeamMembership.role == "admin",
        )
        .first()
    )
    if not m:
        raise HTTPException(status_code=403, detail="Not a team admin")


def _team_to_api(db: Session, team: Team) -> dict:
    rows = (
        db.query(TeamMembership, User)
        .join(User, User.id == TeamMembership.user_id)
        .filter(TeamMembership.team_id == team.id)
        .all()
    )
    members = [
        {
            "user_id": str(m.user_id),
            "email": u.email,
            "display_name": u.display_name,
            "role": m.role,
        }
        for m, u in rows
    ]
    return {
        "id": str(team.id),
        "name": team.name,
        "description": team.description or "",
        "members": members,
    }


@router.get("")
def index(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Team).order_by(Team.name)
    if not user.is_superadmin():
        ids = [m.team_id for m in db.query(TeamMembership).filter(TeamMembership.user_id == user.id).all()]
        q = q.filter(Team.id.in_(ids))
    return [_team_to_api(db, t) for t in q.all()]


@router.post("", status_code=201, dependencies=[Depends(require_superadmin)])
def store(payload: TeamIn, db: Session = Depends(get_db)):
    if db.query(Team).filter(Team.name == payload.name).first():
        raise HTTPException(status_code=409, detail="Team name already exists")
    team = Team(name=payload.name, description=payload.description or "")
    db.add(team)
    db.flush()
    return _team_to_api(db, team)


@router.get("/{team_id}")
def show(team_id: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _team_to_api(db, _find_team(db, team_id))


@router.put("/{team_id}")
def update(
    team_id: str,
    payload: TeamIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = _find_team(db, team_id)
    _assert_can_manage(db, user, team)
    team.name = payload.name
    team.description = payload.description or ""
    db.flush()
    return _team_to_api(db, team)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_superadmin)])
def destroy(team_id: str, db: Session = Depends(get_db)):
    team = _find_team(db, team_id)
    db.delete(team)


@router.post("/{team_id}/members", status_code=201)
def add_member(
    team_id: str,
    payload: MemberIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = _find_team(db, team_id)
    _assert_can_manage(db, user, team)
    target = db.get(User, payload.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    exists = (
        db.query(TeamMembership)
        .filter(TeamMembership.team_id == team.id, TeamMembership.user_id == target.id)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail="User already in team")
    db.add(TeamMembership(team_id=team.id, user_id=target.id, role=payload.role))
    db.flush()
    return _team_to_api(db, team)


@router.put("/{team_id}/members/{user_id}")
def update_member(
    team_id: str,
    user_id: str,
    payload: MemberIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = _find_team(db, team_id)
    _assert_can_manage(db, user, team)
    m = (
        db.query(TeamMembership)
        .filter(TeamMembership.team_id == team.id, TeamMembership.user_id == user_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    m.role = payload.role
    db.flush()
    return _team_to_api(db, team)


@router.delete("/{team_id}/members/{user_id}")
def remove_member(
    team_id: str,
    user_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    team = _find_team(db, team_id)
    _assert_can_manage(db, user, team)
    m = (
        db.query(TeamMembership)
        .filter(TeamMembership.team_id == team.id, TeamMembership.user_id == user_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(m)
    db.flush()
    return _team_to_api(db, team)
