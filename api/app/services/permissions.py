from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ServerOwner, TeamMembership, User


def can_access(db: Session, user: User, server_name: str) -> bool:
    if user.is_superadmin():
        return True
    owner = db.query(ServerOwner).filter(ServerOwner.server_name == server_name).first()
    if owner is None:
        return False
    if str(owner.owner_id) == str(user.id):
        return True
    user_teams = [m.team_id for m in db.query(TeamMembership).filter(TeamMembership.user_id == user.id).all()]
    if not user_teams:
        return False
    shared = (
        db.query(TeamMembership)
        .filter(TeamMembership.user_id == owner.owner_id, TeamMembership.team_id.in_(user_teams))
        .first()
    )
    return shared is not None


def accessible_names(db: Session, user: User) -> list[str] | None:
    """Returns None for superadmins (no filter); a list of names otherwise."""
    if user.is_superadmin():
        return None
    own = [
        s for (s,) in db.query(ServerOwner.server_name).filter(ServerOwner.owner_id == user.id).all()
    ]
    user_team_ids = [
        t for (t,) in db.query(TeamMembership.team_id).filter(TeamMembership.user_id == user.id).all()
    ]
    if not user_team_ids:
        return list(dict.fromkeys(own))
    teammate_ids = [
        u for (u,) in db.query(TeamMembership.user_id).filter(TeamMembership.team_id.in_(user_team_ids)).all()
    ]
    teammate_servers = [
        s for (s,) in db.query(ServerOwner.server_name).filter(ServerOwner.owner_id.in_(teammate_ids)).all()
    ]
    return list(dict.fromkeys([*own, *teammate_servers]))
