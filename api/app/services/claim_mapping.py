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
# Precedence floor for a user who DOES match at least one mapping row: every
# mapped user is at least a "user". This is NOT a fallback for unmapped users —
# claims matching no row are denied access (see resolve_grants / AccessDenied).
_BASE_ROLE = "user"


class AccessDenied(Exception):
    """The verified claims matched no role-mapping row, so the user is entitled
    to nothing. Membership in Roundhouse is mapping-gated: an account with no
    matching mapping is denied sign-in rather than provisioned at a default
    role. The OIDC route turns this into an access-denied login redirect."""


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
    """Project verified claims into dashboard grants ({role, teams}).

    Raises AccessDenied when the claims match no mapping row: Roundhouse access
    is mapping-gated, so an unmapped user is turned away rather than given a
    default role."""
    matched = _matched_mappings(db, extract_app_roles(claims))
    if not matched:
        raise AccessDenied("no role mapping matches the user's Entra app roles")

    role = _BASE_ROLE
    best = _ROLE_PRECEDENCE.get(_BASE_ROLE, 0)
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
