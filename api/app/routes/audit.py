from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.audit import serialize
from app.db import get_db
from app.deps import require_superadmin
from app.models import AuditEvent, User

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_superadmin)])


@router.get("")
def index(
    target_type: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    q = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if target_type:
        q = q.filter(AuditEvent.target_type == target_type)
    if target_id:
        q = q.filter(AuditEvent.target_id == target_id)
    return [serialize(e) for e in q.all()]
