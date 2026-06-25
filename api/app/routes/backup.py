"""Superadmin-only full-platform backup & restore.

GET  /api/backup/info             - what a backup would contain right now
GET  /api/backup/export           - download a .tar.gz of the whole deployment
POST /api/backup/restore/preview  - validate an uploaded backup (dry run)
POST /api/backup/restore          - replace this deployment from a backup

See app.services.backup for the why: all state is in Postgres, so export is a
pg_dump and restore is pg_restore + an orchestrator reconcile.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import audit
from app.db import db_session, get_db
from app.deps import require_superadmin
from app.models import User
from app.services import backup as backup_svc

router = APIRouter(prefix="/api/backup", tags=["backup"])

# A whole-deployment dump is small (specs + assets are capped at 100 MB/server),
# but bound the upload so a stray multi-GB file can't exhaust memory.
MAX_BACKUP_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


@router.get("/info")
def backup_info(_: User = Depends(require_superadmin)) -> dict:
    return backup_svc.deployment_info()


@router.get("/export")
def export_backup(
    db: Session = Depends(get_db),
    me: User = Depends(require_superadmin),
) -> Response:
    try:
        archive, filename = backup_svc.create_backup()
    except backup_svc.BackupError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    audit.record(db, me, "backup.export", "backup", filename, {"size_bytes": len(archive)})
    return Response(
        content=archive,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore/preview")
async def preview_restore(
    file: UploadFile = File(...),
    _: User = Depends(require_superadmin),
) -> dict:
    blob = await _read_upload(file)
    try:
        return backup_svc.restore_preview(blob)
    except backup_svc.BackupError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    force: bool = False,
    db: Session = Depends(get_db),
    me: User = Depends(require_superadmin),
) -> dict:
    blob = await _read_upload(file)
    # Read what we need off the auth user before releasing this request's
    # transaction — pg_restore's --clean DROPs would otherwise block on the
    # AccessShareLocks the auth query is still holding.
    actor_id, actor_email = me.id, me.email
    db.rollback()

    try:
        result = backup_svc.restore_backup(blob, force=force)
    except backup_svc.BackupError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Fresh session: the engine pool was reset during the restore.
    with db_session() as fresh:
        actor = fresh.get(User, actor_id)
        audit.record(
            fresh, actor, "backup.restore", "backup",
            result["manifest"].get("created_at", "unknown"),
            {
                "forced": result["forced"],
                "reconcile": result["reconcile"],
                "actor_email": actor_email,
            },
        )
    return result


async def _read_upload(file: UploadFile) -> bytes:
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload.")
    if len(blob) > MAX_BACKUP_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Backup file is too large.",
        )
    return blob
