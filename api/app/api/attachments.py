from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.attachments.service import VALID_KINDS, delete_attachment_file, read_attachment_bytes, save_attachment
from app.db.models import Attachment, Event

from .deps import get_session

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


def _serialize(attachment: Attachment) -> dict:
    return {
        "id": attachment.id,
        "event_id": attachment.event_id,
        "kind": attachment.kind,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "sha256": attachment.sha256,
        "description": attachment.description,
        "uploaded_at": attachment.uploaded_at.isoformat(),
    }


@router.get("")
def list_attachments(event_id: int | None = None, session: Session = Depends(get_session)):
    query = session.query(Attachment)
    if event_id is not None:
        query = query.filter(Attachment.event_id == event_id)
    return [_serialize(a) for a in query.order_by(Attachment.uploaded_at.desc()).all()]


@router.get("/kinds")
def list_kinds():
    return list(VALID_KINDS)


@router.post("")
async def upload_attachment(
    file: UploadFile = File(...),
    event_id: int | None = Form(None),
    kind: str = Form("other"),
    description: str | None = Form(None),
    session: Session = Depends(get_session),
):
    if event_id is not None and session.get(Event, event_id) is None:
        raise HTTPException(404, "Event not found")
    content = await file.read()
    if not content:
        raise HTTPException(400, "File is empty")
    attachment = save_attachment(
        session,
        content=content,
        filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        kind=kind,
        event_id=event_id,
        description=description,
    )
    session.commit()
    return _serialize(attachment)


def _get_or_404(attachment_id: int, session: Session) -> Attachment:
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(404, "Attachment not found")
    return attachment


@router.get("/{attachment_id}/file")
def download_attachment(attachment_id: int, session: Session = Depends(get_session)):
    attachment = _get_or_404(attachment_id, session)
    try:
        content = read_attachment_bytes(attachment)
    except ValueError as exc:
        raise HTTPException(500, str(exc))
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )


@router.delete("/{attachment_id}")
def delete_attachment(attachment_id: int, session: Session = Depends(get_session)):
    attachment = _get_or_404(attachment_id, session)
    delete_attachment_file(attachment)
    session.delete(attachment)
    session.commit()
    return {"deleted": True}
