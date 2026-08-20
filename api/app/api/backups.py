from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.backup.service import backup_bytes, create_backup, has_backup_today, import_backup, list_backups, prune_backups, restore_backup, verify_backup
from app.core.settings import get_or_create_settings

from .deps import get_session

router = APIRouter(prefix="/api/backups", tags=["backups"])


def _serialize(record) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at.isoformat(),
        "size_bytes": record.size_bytes,
        "verified": record.verified,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
    }


@router.get("")
def list_all(session: Session = Depends(get_session)):
    records = list_backups(session)
    return {
        "backups": [_serialize(r) for r in records],
        "has_backup_today": has_backup_today(session),
    }


@router.post("/run")
def run(session: Session = Depends(get_session)):
    try:
        record = create_backup(session)
        settings = get_or_create_settings(session)
        if settings.backup_verify_after_create:
            verify_backup(session, record.id)
        prune_backups(
            session,
            daily=settings.backup_retention_daily,
            weekly=settings.backup_retention_weekly,
            monthly=settings.backup_retention_monthly,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    return _serialize(record)


@router.post("/upload")
async def upload(file: UploadFile = File(...), session: Session = Depends(get_session)):
    try:
        record = import_backup(session, await file.read())
        settings = get_or_create_settings(session)
        prune_backups(
            session,
            daily=settings.backup_retention_daily,
            weekly=settings.backup_retention_weekly,
            monthly=settings.backup_retention_monthly,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    session.commit()
    return _serialize(record)


@router.post("/{backup_id}/verify")
def verify(backup_id: int, session: Session = Depends(get_session)):
    try:
        record = verify_backup(session, backup_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    session.commit()
    return _serialize(record)


@router.get("/{backup_id}/download")
def download(backup_id: int, session: Session = Depends(get_session)):
    try:
        record, contents = backup_bytes(session, backup_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, str(exc))
    filename = f"ledger-backup-{record.created_at.strftime('%Y%m%dT%H%M%SZ')}.db.enc"
    return Response(
        content=contents,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{backup_id}/restore")
def restore(backup_id: int, session: Session = Depends(get_session)):
    try:
        restored_id = restore_backup(session, backup_id)
    except (OSError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc))
    return {"restored": True, "backup_id": restored_id}
