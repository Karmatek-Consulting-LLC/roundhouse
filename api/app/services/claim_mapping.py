"""Claim -> grant engine.

Input: the verified claims from an OIDC ID token. Output: the Roundhouse grants
those claims entitle the user to, derived from the UI-editable `role_mappings`
table (NOT raw name-matching). See docs/entra-sso-plan.md §2/§3.

This is intentionally the seam Phase 2 reuses: the dashboard wants
`{role, teams}`; the MCP resource-server work will want `{scopes}`. Both start
from the same matched-mappings step (`_matched_mappings`); only the projection
at the end differs. Keep new dashboard logic out of the matching step so the
scope projector can be added later without forking.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import RoleMapping

# Roundhouse top-level role precedence, highest first. When a user's claims map
# to several roundhouse roles, the highest wins.
_ROLE_PRECEDENCE = {"superadmin": 2, "user": 1}
# Role granted to an SSO user whose claims match no mapping row. They can sign in
# (JIT) but get the least privilege; an admin raises them via the mapping table.
_DEFAULT_ROLE = "user"


@dataclass(frozen=True)
class TeamGrant:
    team_id: str
    team_role: str


@dataclass
class Grants:
    """The dashboard projection of a user's claims."""

    role: str
    teams: list[TeamGrant] = field(default_factory=list)


def extract_app_roles(claims: dict) -> list[str]:
    """Pull Entra app roles from the `roles` claim. Entra sends a JSON array;
    tolerate a bare string too. Empty/missing -> []."""
    raw = claims.get("roles")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(r) for r in raw]
    return []


def _matched_mappings(db: Session, app_roles: list[str]) -> list[RoleMapping]:
    """The mapping rows whose entra_app_role matches one of the user's app
    roles. Shared by every projection (dashboard now, scopes in Phase 2)."""
    if not app_roles:
        return []
    return (
        db.query(RoleMapping)
        .filter(RoleMapping.entra_app_role.in_(app_roles))
        .all()
    )


def resolve_grants(db: Session, claims: dict) -> Grants:
    """Project verified claims into dashboard grants ({role, teams})."""
    matched = _matched_mappings(db, extract_app_roles(claims))

    role = _DEFAULT_ROLE
    best = _ROLE_PRECEDENCE.get(_DEFAULT_ROLE, 0)
    teams: dict[str, str] = {}  # team_id -> team_role (last write wins per team)
    for m in matched:
        rank = _ROLE_PRECEDENCE.get(m.roundhouse_role, 0)
        if rank > best:
            best = rank
            role = m.roundhouse_role
        if m.team_id:
            teams[m.team_id] = m.team_role or "member"

    return Grants(
        role=role,
        teams=[TeamGrant(team_id=t, team_role=r) for t, r in teams.items()],
    )
