"""Structured platform logbook — the write side of the admin Logs console.

`record(...)` appends one LogEvent row. Two properties matter more than
anything else here:

  1. It commits in its OWN short-lived session, never the request's. get_db
     rolls back when a handler raises (HTTPException included), and a failed
     login is exactly the event we most need to keep.
  2. It is strictly best-effort: a logging failure is logged to stderr and
     swallowed. Recording must never break authentication itself.

Contexts partition the stream per subsystem (see ALL_CONTEXTS). Successful
mutations mostly arrive via the audit bridge (app.audit.record); explicit
record() calls cover failure paths and events with no audit trail.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db import db_session
from app.models import LogEvent, User

logger = logging.getLogger(__name__)

CONTEXT_AUTH = "auth"        # login / SSO flow / logout / password changes
CONTEXT_DEPLOY = "deploy"    # server lifecycle: create/deploy/start/stop/tokens/scopes/assets
CONTEXT_SCAN = "scan"        # registry vulnerability scanner lookups
CONTEXT_BACKUP = "backup"    # backup export / restore
CONTEXT_ADMIN = "admin"      # users / teams / role mappings / platform settings
CONTEXT_SYSTEM = "system"    # startup, background loops, unhandled errors

ALL_CONTEXTS = (
    CONTEXT_AUTH,
    CONTEXT_DEPLOY,
    CONTEXT_SCAN,
    CONTEXT_BACKUP,
    CONTEXT_ADMIN,
    CONTEXT_SYSTEM,
)

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"
OUTCOME_INFO = "info"

_REDACT_KEYS = {"password", "current_password", "new_password", "token", "cert", "secret"}


def _redact(payload: Any) -> Any:
    """Strip known sensitive keys from a structured payload before persistence.
    Shared with app.audit (which imports it from here)."""
    if isinstance(payload, dict):
        out: dict = {}
        for k, v in payload.items():
            if k.lower() in _REDACT_KEYS:
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(payload, list):
        return [_redact(x) for x in payload]
    return payload


def client_ip(request: Request | None) -> str | None:
    """Original client IP. Behind Traefik the socket peer is the proxy, so
    prefer the first hop of X-Forwarded-For when present."""
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else None


def build_event(
    context: str,
    event_type: str,
    outcome: str,
    *,
    request: Request | None = None,
    user: User | None = None,
    email: str | None = None,
    message: str | None = None,
    detail: dict | None = None,
) -> LogEvent:
    """Assemble (but don't persist) a LogEvent. Split from record() so tests
    can exercise field mapping/redaction against any session."""
    ua = request.headers.get("user-agent") if request is not None else None
    return LogEvent(
        context=context,
        event_type=event_type,
        outcome=outcome,
        actor_id=str(user.id) if user is not None else None,
        actor_email=(email or (user.email if user is not None else None)),
        ip=client_ip(request),
        user_agent=ua[:512] if ua else None,
        message=message,
        detail=_redact(detail) if detail else None,
    )


def record(
    context: str,
    event_type: str,
    outcome: str,
    *,
    request: Request | None = None,
    user: User | None = None,
    email: str | None = None,
    message: str | None = None,
    detail: dict | None = None,
    db: Session | None = None,
) -> None:
    """Append one log event; never raises.

    With `db` the event joins the caller's transaction — use for success
    events recorded alongside a mutation, so a later rollback takes the log
    entry with it. Without `db` it commits in its own short-lived session —
    use for failure paths, where the request transaction is about to roll
    back and the entry must survive anyway.
    """
    try:
        event = build_event(
            context,
            event_type,
            outcome,
            request=request,
            user=user,
            email=email,
            message=message,
            detail=detail,
        )
        if db is not None:
            db.add(event)
        else:
            with db_session() as s:
                s.add(event)
    except Exception:  # noqa: BLE001 - logging must never break the caller
        logger.exception("failed to record %s/%s log event", context, event_type)


def serialize(event: LogEvent) -> dict:
    return {
        "id": event.id,
        "ts": event.ts.isoformat() if event.ts else None,
        "context": event.context,
        "event_type": event.event_type,
        "outcome": event.outcome,
        "actor_id": event.actor_id,
        "actor_email": event.actor_email,
        "ip": event.ip,
        "user_agent": event.user_agent,
        "message": event.message,
        "detail": event.detail,
    }
