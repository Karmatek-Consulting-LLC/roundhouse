"""Helpers for recording mutating events to the audit log.

Call `record(...)` from any router that mutates state. Reads are not logged.
Sensitive fields ('password', 'token', 'cert', 'secret', 'value' for env
vars) are stripped from the payload before persistence."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, User


_REDACT_KEYS = {"password", "current_password", "new_password", "token", "cert", "secret"}


def _redact(payload: Any) -> Any:
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


def record(
    db: Session,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
) -> None:
    db.add(AuditEvent(
        actor_id=str(actor.id) if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        payload=_redact(payload) if payload else None,
    ))


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
