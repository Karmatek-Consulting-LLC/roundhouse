"""Pluggable assertion profiles for the jwt-bearer grant (RFC 7523).

This module IS the ID-JAG migration surface (docs/mcp-auth-id-jag.md §7/§8):
a profile is a named bundle of (expected issuer, expected audience, accepted
typ, replay policy). The token endpoint logic is profile-agnostic; "interim
mode" and "ID-JAG mode" are two rows here, and both can be enabled at once
during cutover.

    profile            assertion accepted      aud check                 jti
    entra-id-token     user's Entra id_token   the CLIENT's Entra app id  no*
    id-jag             oauth-id-jag+jwt        OUR issuer URL             single-use

* One id_token is legitimately exchanged for many per-server tokens while it
  lives (that's the whole caching story), so the interim profile must NOT
  enforce single-use. The ID-JAG is a one-time letter of introduction, so the
  id-jag profile must.

Configuration lives in platform_settings[oauth_assertion_profiles] as a JSON
list. When unset, a default entra-id-token profile is derived from the shipped
Entra SSO connection (same tenant, JWKS, validation code path) with the
audience left empty — an operator must set the harness's Entra client id
before the grant works, which is deliberate: it is an allowlist, not a default.

    [{"name": "entra-id-token", "enabled": true,
      "audience": "<harness Entra app client id>"},
     {"name": "id-jag", "enabled": false}]
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from authlib.jose import JsonWebToken
from authlib.jose.errors import JoseError
from sqlalchemy.orm import Session

from app.models import OAuthUsedJti, User
from app.platform_settings import SETTING_OAUTH_ASSERTION_PROFILES, get_setting
from app.services import oauth_tokens, sso_config
from app.services.oidc import OidcClient, OidcError

_jwt = JsonWebToken(["RS256"])

# The typ the ID-JAG draft mandates for its assertion JWTs.
_ID_JAG_TYP = "oauth-id-jag+jwt"


class AssertionError_(ValueError):
    """Assertion failed validation. Maps to invalid_grant with this detail."""


@dataclass(frozen=True)
class AssertionProfile:
    name: str  # "entra-id-token" | "id-jag"
    enabled: bool
    issuer: str  # expected iss (the IdP)
    audience: str  # expected aud (client id for interim; our issuer for id-jag)
    discovery_url: str  # where the IdP's JWKS lives
    require_typ: str | None = None  # header typ that must match (id-jag)
    single_use_jti: bool = False


@dataclass
class VerifiedAssertion:
    profile: AssertionProfile
    claims: dict
    user: User


def load_profiles(db: Session) -> list[AssertionProfile]:
    """Profiles from settings, with issuer/JWKS defaults filled from the Entra
    SSO connection so the operator configures only what's genuinely new."""
    entra = sso_config.load(db)
    raw = get_setting(db, SETTING_OAUTH_ASSERTION_PROFILES) or ""
    rows: list[dict] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                rows = [r for r in parsed if isinstance(r, dict)]
        except ValueError:
            rows = []
    if not rows:
        rows = [{"name": "entra-id-token", "enabled": True, "audience": ""}]

    out: list[AssertionProfile] = []
    for r in rows:
        name = str(r.get("name") or "").strip()
        if name not in ("entra-id-token", "id-jag"):
            continue
        issuer = str(r.get("issuer") or "").strip() or (
            entra.issuer if entra.tenant_id else ""
        )
        discovery = str(r.get("discovery_url") or "").strip() or (
            entra.discovery_url if entra.tenant_id else ""
        )
        if name == "id-jag":
            audience = str(r.get("audience") or "").strip() or oauth_tokens.issuer()
            out.append(
                AssertionProfile(
                    name=name,
                    enabled=bool(r.get("enabled", False)),
                    issuer=issuer,
                    audience=audience,
                    discovery_url=discovery,
                    require_typ=_ID_JAG_TYP,
                    single_use_jti=True,
                )
            )
        else:
            out.append(
                AssertionProfile(
                    name=name,
                    enabled=bool(r.get("enabled", True)),
                    issuer=issuer,
                    audience=str(r.get("audience") or "").strip(),
                    discovery_url=discovery,
                )
            )
    return out


def _idp_validator(profile: AssertionProfile) -> OidcClient:
    """Reuse the shipped OIDC client purely for its JWKS cache + rotation-aware
    decode — the 'same code path as dashboard SSO' promise in the design."""
    return OidcClient(
        discovery_url=profile.discovery_url,
        issuer=profile.issuer,
        client_id=profile.audience,  # aud check target
        client_secret="",
        redirect_uri="",
    )


def _decode(profile: AssertionProfile, assertion: str) -> dict:
    client = _idp_validator(profile)
    claims_options = {
        "iss": {"essential": True, "value": profile.issuer},
        "aud": {"essential": True, "value": profile.audience},
        "exp": {"essential": True},
    }
    try:
        claims = client._decode_with_rotation(assertion, claims_options)
        claims.validate()
    except (OidcError, JoseError, ValueError) as e:
        raise AssertionError_(f"assertion rejected by profile {profile.name}: {e}") from e
    return dict(claims)


def _check_typ(profile: AssertionProfile, assertion: str) -> None:
    if not profile.require_typ:
        return
    import base64

    try:
        header_b64 = assertion.split(".", 1)[0]
        header = json.loads(
            base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4))
        )
    except (ValueError, IndexError) as e:
        raise AssertionError_("assertion header unreadable") from e
    if str(header.get("typ") or "").lower() != profile.require_typ:
        raise AssertionError_(
            f"assertion typ must be {profile.require_typ} for profile {profile.name}"
        )


def _consume_jti(db: Session, claims: dict) -> None:
    jti = str(claims.get("jti") or "")
    if not jti:
        raise AssertionError_("assertion has no jti (required for single-use grants)")
    if db.get(OAuthUsedJti, jti) is not None:
        raise AssertionError_("assertion replayed (jti already used)")
    exp = claims.get("exp")
    expires = (
        datetime.fromtimestamp(int(exp), tz=timezone.utc)
        if isinstance(exp, (int, float))
        else datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    db.add(OAuthUsedJti(jti=jti, expires_at=expires))
    # Opportunistic prune so the table can't grow unbounded.
    db.query(OAuthUsedJti).filter(
        OAuthUsedJti.expires_at < datetime.now(timezone.utc)
    ).delete()


def _resolve_user(db: Session, claims: dict) -> User:
    """Map assertion claims to an existing platform user. Deliberately NO JIT
    provisioning here: the token endpoint runs with no human present, so an
    unknown subject is a policy failure, not an onboarding moment. (Users are
    provisioned by dashboard SSO login or by an admin.)"""
    sub = str(claims.get("oid") or claims.get("sub") or "")
    email = str(
        claims.get("preferred_username") or claims.get("email") or ""
    ).strip().lower()
    user = None
    if sub:
        user = db.query(User).filter(User.oidc_sub == sub).first()
    if user is None and email:
        user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise AssertionError_(
            "assertion subject is not a Roundhouse user (sign in to the dashboard "
            "once, or ask an admin to provision the account)"
        )
    return user


def validate_assertion(db: Session, assertion: str) -> VerifiedAssertion:
    """Try every enabled profile; first cryptographic match wins. Signature,
    iss, aud, exp per profile; typ + single-use jti where the profile says so;
    then subject resolution against the user table."""
    profiles = [p for p in load_profiles(db) if p.enabled]
    ready = [p for p in profiles if p.issuer and p.audience and p.discovery_url]
    if not ready:
        raise AssertionError_(
            "no assertion profile is configured (set oauth_assertion_profiles, "
            "and configure Entra SSO for the issuer defaults)"
        )
    errors: list[str] = []
    for profile in ready:
        try:
            _check_typ(profile, assertion)
            claims = _decode(profile, assertion)
        except AssertionError_ as e:
            errors.append(str(e))
            continue
        if profile.single_use_jti:
            _consume_jti(db, claims)
        user = _resolve_user(db, claims)
        return VerifiedAssertion(profile=profile, claims=claims, user=user)
    raise AssertionError_("; ".join(errors) or "assertion matched no enabled profile")
