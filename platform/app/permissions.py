from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db_models import ServerOwner, TeamMembership, User


def can_access_server(user: User, server_name: str, db: Session) -> bool:
    """Check if a user can access/manage a server."""
    if user.role == "superadmin":
        return True

    owner = db.query(ServerOwner).filter(ServerOwner.server_name == server_name).first()
    if not owner:
        return False

    # Owner can always access their own server
    if owner.owner_id == user.id:
        return True

    # Check if user shares a team with the owner
    user_teams = set(
        r[0] for r in db.execute(
            select(TeamMembership.team_id).where(TeamMembership.user_id == user.id)
        ).all()
    )
    owner_teams = set(
        r[0] for r in db.execute(
            select(TeamMembership.team_id).where(TeamMembership.user_id == owner.owner_id)
        ).all()
    )
    return bool(user_teams & owner_teams)


def get_accessible_server_names(user: User, db: Session) -> set[str] | None:
    """Get set of server names a user can access, or None for superadmin (all)."""
    if user.role == "superadmin":
        return None

    # Own servers
    own = set(
        r[0] for r in db.execute(
            select(ServerOwner.server_name).where(ServerOwner.owner_id == user.id)
        ).all()
    )

    # Servers owned by teammates
    user_teams = select(TeamMembership.team_id).where(
        TeamMembership.user_id == user.id
    ).scalar_subquery()

    teammate_servers = set(
        r[0] for r in db.execute(
            select(ServerOwner.server_name)
            .join(TeamMembership, TeamMembership.user_id == ServerOwner.owner_id)
            .where(TeamMembership.team_id.in_(select(user_teams)))
        ).all()
    )

    return own | teammate_servers
