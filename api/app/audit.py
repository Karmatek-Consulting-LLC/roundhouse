"""Helpers for recording mutating events to the audit log.

Call `record(...)` from any router that mutates state. Reads are not logged.
Sensitive fields ('password', 'token', 'cert', 'secret', 'value' for env
vars) are stripped from the payload before persistence.

Every audit event is also bridged into the Logs console stream (log_events)
under the context matching its target_type, in the SAME session — so a
mutation that rolls back takes both records with it."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import logbook
from app.logbook import _redact
from app.models import AuditEvent, User

# Which Logs-console context an audited mutation lands in.
_CONTEXT_BY_TARGET: dict[str, str] = {
    "server": logbook.CONTEXT_DEPLOY,
    "server_token": logbook.CONTEXT_DEPLOY,
    "server_scope": logbook.CONTEXT_DEPLOY,
    "backup": logbook.CONTEXT_BACKUP,
    "user": logbook.CONTEXT_ADMIN,
    "team": logbook.CONTEXT_ADMIN,
    "role_mapping": logbook.CONTEXT_ADMIN,
    "settings": logbook.CONTEXT_ADMIN,
}


def record(
    db: Session,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
) -> None:
    redacted = _redact(payload) if payload else None
    db.add(AuditEvent(
        actor_id=str(actor.id) if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        payload=redacted,
    ))
    logbook.record(
        _CONTEXT_BY_TARGET.get(target_type, logbook.CONTEXT_ADMIN),
        action,
        logbook.OUTCOME_SUCCESS,
        user=actor,
        message=f"{action}: {target_id}",
        detail=redacted,
        db=db,
    )


def serialize(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "actor_id": event.actor_id,
        "actor_email": event.actor_email,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
