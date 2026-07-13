"""Structured platform logbook — the write side of the admin Logs console.

`record(...)` appends one LogEvent row. Two properties matter more than
anything else here:

  1. It commits in its OWN short-lived session, never the request's. get_db
     rolls back when a handler raises (HTTPException included), and a failed
     login is exactly the event we most need to keep.
  2. It is strictly best-effort: a logging failure is logged to stderr and
     swallowed. Recording must never break authentication itself.

The first context is "auth" (local login, SSO/OIDC flow, logout). Future
contexts (deployments, registry scans, ...) reuse the same table/API by
recording with a new context string and whitelisting it in routes/logs.py.
"""
from __future__ import annotations

import logging

from fastapi import Request

from app.audit import _redact
from app.db import db_session
from app.models import LogEvent, User

logger = logging.getLogger(__name__)

CONTEXT_AUTH = "auth"

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"
OUTCOME_INFO = "info"


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
) -> None:
    """Append one log event. Own session, commits immediately, never raises."""
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
        with db_session() as db:
            db.add(event)
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
