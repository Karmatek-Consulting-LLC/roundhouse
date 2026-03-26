from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_superadmin
from app.database import get_db
from app.db_models import Team, TeamMembership, User
from app.models import (
    TeamMemberRequest,
    TeamMemberResponse,
    TeamRequest,
    TeamResponse,
)

router = APIRouter()


def _team_response(team: Team) -> TeamResponse:
    members = [
        TeamMemberResponse(
            user_id=str(m.user_id),
            email=m.user.email,
            display_name=m.user.display_name,
            role=m.role,
        )
        for m in team.memberships
    ]
    return TeamResponse(
        id=str(team.id),
        name=team.name,
        description=team.description,
        members=members,
    )


def _can_manage_team(user: User, team: Team, db: Session) -> bool:
    if user.role == "superadmin":
        return True
    membership = db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id,
        TeamMembership.user_id == user.id,
        TeamMembership.role == "admin",
    ).first()
    return membership is not None


@router.get("/teams", response_model=list[TeamResponse])
def list_teams(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role == "superadmin":
        teams = db.query(Team).order_by(Team.name).all()
    else:
        team_ids = [m.team_id for m in user.memberships]
        teams = db.query(Team).filter(Team.id.in_(team_ids)).order_by(Team.name).all()
    return [_team_response(t) for t in teams]


@router.post("/teams", response_model=TeamResponse, status_code=201)
def create_team(
    req: TeamRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    existing = db.query(Team).filter(Team.name == req.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Team name already exists")

    team = Team(name=req.name, description=req.description)
    db.add(team)
    db.commit()
    db.refresh(team)
    return _team_response(team)


@router.get("/teams/{team_id}", response_model=TeamResponse)
def get_team(
    team_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return _team_response(team)


@router.put("/teams/{team_id}", response_model=TeamResponse)
def update_team(
    team_id: str,
    req: TeamRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not _can_manage_team(user, team, db):
        raise HTTPException(status_code=403, detail="Not a team admin")

    team.name = req.name
    team.description = req.description
    db.commit()
    db.refresh(team)
    return _team_response(team)


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(
    team_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_superadmin),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    db.delete(team)
    db.commit()


@router.post("/teams/{team_id}/members", response_model=TeamResponse, status_code=201)
def add_member(
    team_id: str,
    req: TeamMemberRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not _can_manage_team(user, team, db):
        raise HTTPException(status_code=403, detail="Not a team admin")

    target_user = db.query(User).filter(User.id == req.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id,
        TeamMembership.user_id == target_user.id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="User already in team")

    db.add(TeamMembership(user_id=target_user.id, team_id=team.id, role=req.role))
    db.commit()
    db.refresh(team)
    return _team_response(team)


@router.put("/teams/{team_id}/members/{user_id}", response_model=TeamResponse)
def update_member_role(
    team_id: str,
    user_id: str,
    req: TeamMemberRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not _can_manage_team(user, team, db):
        raise HTTPException(status_code=403, detail="Not a team admin")

    membership = db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id,
        TeamMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    membership.role = req.role
    db.commit()
    db.refresh(team)
    return _team_response(team)


@router.delete("/teams/{team_id}/members/{user_id}", response_model=TeamResponse)
def remove_member(
    team_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not _can_manage_team(user, team, db):
        raise HTTPException(status_code=403, detail="Not a team admin")

    membership = db.query(TeamMembership).filter(
        TeamMembership.team_id == team.id,
        TeamMembership.user_id == user_id,
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    db.delete(membership)
    db.commit()
    db.refresh(team)
    return _team_response(team)
