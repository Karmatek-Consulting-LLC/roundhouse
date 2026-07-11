from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import ServerOwner, User
from app.services import permissions, registry_scan

router = APIRouter(prefix="/api/vulnerabilities", tags=["vulnerabilities"])


@router.get("")
def index(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Vulnerability summaries (from the registry's scanner, e.g. Harbor/Trivy)
    for every server the caller can access. `available: false` when no scanner
    is configured — the UI hides the badges entirely in that case."""
    if not registry_scan.enabled(db):
        return {"available": False, "servers": {}}
    names = permissions.accessible_names(db, user)
    if names is None:  # superadmin - all registered servers
        names = [n for (n,) in db.query(ServerOwner.server_name).all()]
    return {
        "available": True,
        "servers": registry_scan.get_scanner().summaries(db, sorted(set(names))),
    }
