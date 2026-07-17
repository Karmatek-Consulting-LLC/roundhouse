"""SQLAlchemy models for the MCP Platform schema.

Tables:
  - users
  - teams
  - team_memberships
  - role_mappings
  - server_owners
  - platform_settings
  - server_scopes
  - server_tokens
  - personal_access_tokens
  - cache, cache_locks, jobs, job_batches, failed_jobs, sessions  (legacy — declared but unused)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    pass


def _uuid_str() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), primary_key=True, default=_uuid_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Nullable since the Entra SSO work: Entra-only users authenticate via OIDC
    # and have no local password. Local (break-glass) users still set one.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    # How this user authenticates. "local" users are break-glass and exempt from
    # SSO sync; "entra" users are re-synced from claims on every login.
    auth_source: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    # Entra `sub` (subject) claim — the stable per-user, per-app identifier we
    # match on across logins. Unique when present, NULL for local users.
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list["TeamMembership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    owned_servers: Mapped[list["ServerOwner"]] = relationship(back_populates="owner")

    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    def to_api(self) -> dict:
        return {
            "id": str(self.id),
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "auth_source": self.auth_source,
        }


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), primary_key=True, default=_uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list["TeamMembership"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )


class TeamMembership(Base):
    __tablename__ = "team_memberships"

    user_id: Mapped[str] = mapped_column(
        PgUUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    team_id: Mapped[str] = mapped_column(
        PgUUID(as_uuid=False),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)

    user: Mapped["User"] = relationship(back_populates="memberships")
    team: Mapped["Team"] = relationship(back_populates="memberships")


class RoleMapping(Base):
    """Entra app role -> Roundhouse grant. The claim->grant engine reads these
    rows to translate an SSO user's `roles` claim into a Roundhouse role and
    (optionally) a team membership. This is the authoritative, UI-editable
    mapping table that replaces raw name-matching; see docs/entra-sso-plan.md.

    A single Entra app role may produce both a top-level role and a team grant.
    Phase 2 (MCP) will reuse the same table shape to emit scopes."""

    __tablename__ = "role_mappings"
    __table_args__ = (
        UniqueConstraint("entra_app_role", name="role_mappings_entra_app_role_unique"),
    )

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    # The value as it appears in the Entra `roles` claim (app role value).
    entra_app_role: Mapped[str] = mapped_column(String(255), nullable=False)
    # Roundhouse top-level role to grant (e.g. "superadmin" | "user"). Required.
    roundhouse_role: Mapped[str] = mapped_column(String(20), nullable=False)
    # Optional team grant: when team_id is set, the user is added to that team
    # with team_role (default "member") during sync.
    team_id: Mapped[str | None] = mapped_column(
        PgUUID(as_uuid=False),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    team_role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ServerOwner(Base):
    __tablename__ = "server_owners"

    server_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        PgUUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Renamed from auth_rebuild_required_at by the 2026_05_22 migration. Set
    # when any spec change happens; cleared on successful redeploy.
    redeploy_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped["User"] = relationship(back_populates="owned_servers")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ServerScope(Base):
    __tablename__ = "server_scopes"
    __table_args__ = (
        UniqueConstraint("server_name", "name", name="server_scopes_server_name_name_unique"),
        Index("server_scopes_server_name_index", "server_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ServerToken(Base):
    __tablename__ = "server_tokens"
    __table_args__ = (
        UniqueConstraint("server_name", "name", name="server_tokens_server_name_name_unique"),
        Index("server_tokens_server_name_index", "server_name"),
    )

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Encrypted at rest with the AES-256-CBC + HMAC envelope keyed off APP_KEY
    # (see app.crypto). Fixed format — existing rows must remain decryptable.
    token: Mapped[str] = mapped_column(Text, nullable=False)
    display_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # JSON-encoded list[str] of scope names this token grants.
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditEvent(Base):
    """Append-only log of mutating operations. Recorded by app.audit.record()
    from the routers that mutate state. Read by the /api/audit endpoint."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("audit_events_created_at_index", "created_at"),
        Index("audit_events_target_index", "target_type", "target_id"),
    )

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    actor_id: Mapped[str | None] = mapped_column(PgUUID(as_uuid=False), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Small structured payload describing what changed (e.g. {"replicas": 4}).
    # Don't store secrets here - the recording helpers strip known sensitive keys.
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LogEvent(Base):
    """Append-only platform log stream, partitioned by `context`. The first
    context is "auth" (login / SSO / logout activity); deployments, scans, etc.
    slot in later without schema changes. Written via app.logbook.record(),
    which commits in its own session so entries survive the request rollback
    on failure paths (a failed login must still be logged). Read by the
    superadmin /api/logs console (list/search/SSE stream/export). Pruned on a
    retention window (see app.services.event_retention)."""

    __tablename__ = "log_events"
    __table_args__ = (
        # The console always filters by context, then ranges/orders by time.
        Index("log_events_context_ts_index", "context", "ts"),
        # The retention DELETE scans ts alone.
        Index("log_events_ts_index", "ts"),
    )

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    context: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "auth"
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "login", "sso.callback"
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # success | failure | denied | info
    # Who the event is about. Nullable: a failed login has no resolved user,
    # and the email is whatever was attempted (useful for troubleshooting).
    actor_id: Mapped[str | None] = mapped_column(PgUUID(as_uuid=False), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Short human-readable summary, mirrors what the end user saw (e.g. the
    # SSO error surfaced on the login page).
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Small structured payload. Recording helpers strip known sensitive keys.
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class PersonalAccessToken(Base):
    """API bearer tokens. Plaintext form is `{id}|{rawText}`; the column
    stores sha256(rawText) — never the raw token."""

    __tablename__ = "personal_access_tokens"
    __table_args__ = (Index("personal_access_tokens_expires_at_index", "expires_at"),)

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    tokenable_type: Mapped[str] = mapped_column(String(255), nullable=False)
    tokenable_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    abilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RequestEvent(Base):
    """One MCP primitive invocation, captured as metadata only (never request
    arguments or response bodies). Pushed by each spawned server's middleware
    to /api/ingest/events and read back by the /api/observability/* console.

    This is the platform's persistent, queryable history — distinct from the
    point-in-time in-process counters scraped via /metrics. Pruned on a
    retention window (see app.services.event_retention)."""

    __tablename__ = "request_events"
    __table_args__ = (
        # Per-server timeseries/feed/top all filter by server_name then order/range by ts.
        Index("request_events_server_ts_index", "server_name", "ts"),
        # Cross-server (superadmin) queries + the retention DELETE scan by ts alone.
        Index("request_events_ts_index", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # When the call completed, as reported by the originating server (UTC).
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # tool | resource | prompt
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # primitive name / uri
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    # Exception type name when the call failed, else NULL.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalized from `error` so error-rate filters are a clean indexed predicate.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")  # ok | error


class Server(Base):
    """Authoritative per-server definition. Replaces the old per-node
    `server.json` on the `server-data` Swarm volume so platform-api can scale
    out (no node-local state). `spec` is the lossless ServerSpec.to_dict()
    JSON; `build_files` is a gzip tarball of the non-regenerable build context
    (cloned git repo / rendered template files) materialized into a temp dir
    at build time. Generated server.py/Dockerfile are NOT stored — codegen
    rewrites them each build."""

    __tablename__ = "servers"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="structured")
    build_files: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ServerAsset(Base):
    """Per-server uploaded asset, baked into the spawned image at build time.
    Moved off the per-node volume into Postgres alongside the spec. Bounded by
    the app layer (10 MB/file, 100 MB/server) so bytea stays modest."""

    __tablename__ = "server_assets"
    __table_args__ = (
        UniqueConstraint("server_name", "filename", name="server_assets_name_uq"),
        Index("server_assets_server_index", "server_name"),
    )

    # BigInteger on Postgres; INTEGER on SQLite so it aliases rowid and
    # autoincrements (BIGINT does not) — keeps the model usable in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OAuthClient(Base):
    """A registered OAuth client of the Roundhouse authorization server.

    Three registration types (docs/mcp-auth-id-jag.md §7, field guide §05):
      - manual: admin-created (e.g. Valkyrie). May be `trusted`.
      - dcr:    self-registered via RFC 7591 /oauth/register. Never trusted.
      - cimd:   client_id IS an https URL serving the client's metadata; the
                row is a cache of the fetched document. Never trusted.

    `trusted` gates the two privileged behaviours: consent is skipped, and the
    jwt-bearer grant (acting for a user with no user present) is allowed."""

    __tablename__ = "oauth_clients"

    # CIMD client_ids are URLs, so allow generous length.
    client_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    # sha256 hex of the client secret; NULL for public clients (auth method "none").
    client_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none"
    )  # none | client_secret_basic | client_secret_post
    redirect_uris: Mapped[list | None] = mapped_column(JSON, nullable=True)
    grant_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    registration_type: Mapped[str] = mapped_column(String(16), nullable=False, default="dcr")
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthAuthCode(Base):
    """One-time authorization code (authorization_code + PKCE flow). The code
    itself is never stored — only its sha256 — and it is single-use: consumed_at
    marks redemption, and a second redemption attempt is an attack signal."""

    __tablename__ = "oauth_auth_codes"
    __table_args__ = (Index("oauth_auth_codes_expires_index", "expires_at"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), nullable=False)
    # RFC 8707 resource the token will be audience-bound to (canonical /s/{name}/mcp URL).
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False, default="S256")
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OAuthRefreshToken(Base):
    """Rotating refresh token (OAuth 2.1 mandates rotation for public clients;
    we rotate for everyone). Reuse of a rotated token is detected via
    revoked_at + replaced_by and revokes the whole (user, client) family."""

    __tablename__ = "oauth_refresh_tokens"
    __table_args__ = (
        Index("oauth_refresh_tokens_user_client_index", "user_id", "client_id"),
        Index("oauth_refresh_tokens_expires_index", "expires_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), nullable=False)
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthConsent(Base):
    """Remembered per-(user, client) consent so the approval screen shows once,
    not once per MCP server (field guide: 'consent skipped or remembered per
    (user, client) — not re-shown per server')."""

    __tablename__ = "oauth_consents"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="oauth_consents_user_client_uq"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    user_id: Mapped[str] = mapped_column(PgUUID(as_uuid=False), nullable=False)
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthUsedJti(Base):
    """Single-use assertion ids (ID-JAG `jti` replay defence). Only the id-jag
    assertion profile writes here — the interim entra-id-token profile must NOT
    (one Entra id_token is legitimately exchanged for many per-server tokens;
    see docs/mcp-auth-id-jag.md §7). Rows are prunable once expired."""

    __tablename__ = "oauth_used_jtis"
    __table_args__ = (Index("oauth_used_jtis_expires_index", "expires_at"),)

    jti: Mapped[str] = mapped_column(String(255), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
