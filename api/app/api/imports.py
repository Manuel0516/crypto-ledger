from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.connectors.bitget import BitgetConnector
from app.core.ledger.service import ingest, record_sync

from .deps import get_session

router = APIRouter(prefix="/api/import", tags=["imports"])
_BITGET = BitgetConnector()


@router.post("/bitget")
async def import_bitget(session: Session = Depends(get_session), file: UploadFile = File(...)):
    data = await file.read()
    try:
        records = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(400, "Upload a JSON array exported by the connector")
    if not isinstance(records, list):
        raise HTTPException(400, "Expected a JSON array")

    imported = 0
    skipped = 0
    try:
        raw_records = _BITGET.fetch_records(records)
        for raw in raw_records:
            event = ingest(session, _BITGET, raw)
            if event is None:
                skipped += 1
            else:
                imported += 1
    except (TypeError, ValueError) as exc:
        session.rollback()
        raise HTTPException(400, str(exc)) from exc

    record_sync(session, _BITGET.source_id, imported)
    session.commit()
    return {"imported": imported, "skipped_duplicates": skipped}
