"""SSO user provisioning + sync.

Glue between verified OIDC claims and the Roundhouse user model:

  - upsert_sso_user: JIT-provision (or find) the user behind a set of claims.
  - sync_grants:     re-apply role + team memberships from the claim->grant
                     engine. Entra is authoritative for SSO users, so this runs
                     on every login and gives real deprovisioning.

Two invariants are enforced here, not in the routes:
  1. Local (break-glass) users are NEVER touched by sync (decision #5).
  2. Sync NEVER demotes the last remaining superadmin (a hard floor that keeps
     an admin from locking everyone out via a bad mapping table).
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import TeamMembership, User
from app.services.claim_mapping import Grants

logger = logging.getLogger("roundhouse-api")


class SsoError(RuntimeError):
    """Provisioning could not proceed (e.g. an email already belongs to a local
    account). Mapped to an auth failure by the route."""


def _email_from_claims(claims: dict) -> str | None:
    # Entra v2.0 puts the address in `email`; fall back to preferred_username /
    # upn, which carry the UPN (usually an email) for work/school accounts.
    for key in ("email", "preferred_username", "upn"):
        val = claims.get(key)
        if val:
            return str(val).lower()
    return None


def _display_name_from_claims(claims: dict, fallback: str) -> str:
    return str(claims.get("name") or fallback)


def upsert_sso_user(db: Session, claims: dict) -> User:
    """Find or JIT-create the user behind these claims.

    Match order: oidc_sub (stable), then email. An email that already belongs to
    a *local* account is refused rather than silently converted — that protects
    the break-glass guarantee and prevents email-collision account takeover."""
    sub = claims.get("sub")
    if not sub:
        raise SsoError("ID token has no subject (sub) claim")
    email = _email_from_claims(claims)
    if not email:
        raise SsoError("ID token carries no email/upn to identify the user")

    user = db.query(User).filter(User.oidc_sub == str(sub)).first()
    if user is not None:
        # Keep email/display_name fresh from the IdP.
        user.email = email
        user.display_name = _display_name_from_claims(claims, user.display_name)
        return user

    existing = db.query(User).filter(func.lower(User.email) == email).first()
    if existing is not None:
        if existing.auth_source != "entra":
            raise SsoError(
                f"An account for {email} already exists as a local user; "
                "an administrator must migrate it before SSO can be used."
            )
        # An entra user whose sub changed (rare — app re-registration). Adopt it.
        existing.oidc_sub = str(sub)
        existing.display_name = _display_name_from_claims(claims, existing.display_name)
        return existing

    user = User(
        email=email,
        password_hash=None,
        display_name=_display_name_from_claims(claims, email),
        role="user",  # provisional; sync_grants sets the real role next.
        auth_source="entra",
        oidc_sub=str(sub),
    )
    db.add(user)
    db.flush()
    logger.info("JIT-provisioned SSO user %s (sub=%s)", email, sub)
    return user


def _would_orphan_last_superadmin(db: Session, user: User, new_role: str) -> bool:
    """True if demoting `user` from superadmin to `new_role` would leave the
    platform with zero superadmins."""
    if user.role != "superadmin" or new_role == "superadmin":
        return False
    others = (
        db.query(User)
        .filter(User.role == "superadmin", User.id != user.id)
        .count()
    )
    return others == 0


def sync_grants(db: Session, user: User, grants: Grants) -> None:
    """Re-apply role + team memberships from the mapping engine.

    No-op for local users. Role demotion is suppressed if it would remove the
    last superadmin. Team memberships are made to exactly match the grants
    (authoritative), giving real deprovisioning for SSO users."""
    if user.auth_source != "entra":
        return  # break-glass / local users are exempt from sync

    if _would_orphan_last_superadmin(db, user, grants.role):
        logger.warning(
            "Refusing to demote last superadmin %s via SSO sync; keeping superadmin",
            user.email,
        )
    else:
        user.role = grants.role

    _sync_team_memberships(db, user, grants)


def _sync_team_memberships(db: Session, user: User, grants: Grants) -> None:
    desired = {g.team_id: (g.team_role or "member") for g in grants.teams}
    current = {m.team_id: m for m in user.memberships}

    # Drop memberships the claims no longer grant.
    for team_id, membership in current.items():
        if team_id not in desired:
            db.delete(membership)

    # Add or update granted memberships.
    for team_id, team_role in desired.items():
        membership = current.get(team_id)
        if membership is None:
            db.add(TeamMembership(user_id=user.id, team_id=team_id, role=team_role))
        elif membership.role != team_role:
            membership.role = team_role
