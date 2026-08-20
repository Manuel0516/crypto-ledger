from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.reporting.accountant_pdf import render_accountant_pdf
from app.core.reporting.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.core.reporting.ledger_csv import export_ledger_csv
from app.core.reporting.evidence import export_evidence_archive, verify_evidence_archive
from app.core.reporting.readiness import get_readiness

from .deps import get_session

router = APIRouter(prefix="/api/reports", tags=["reports"])
export_router = APIRouter(prefix="/api/export", tags=["exports"])


@router.get("/readiness")
def readiness(session: Session = Depends(get_session)):
    return get_readiness(session)


@router.get("/ledger.csv")
@export_router.get("/ledger.csv")
def ledger_csv(session: Session = Depends(get_session)):
    csv_text = export_ledger_csv(session)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=crypto-ledger.csv"},
    )


@router.get("/evidence.zip")
@export_router.get("/evidence.zip")
def evidence_archive(report_id: int | None = None, session: Session = Depends(get_session)):
    archive = export_evidence_archive(session, report_id=report_id)
    return StreamingResponse(
        iter([archive]),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=crypto-ledger-evidence.zip"},
    )


@router.get("/accountant.pdf")
@export_router.get("/accountant.pdf")
def accountant_pdf(language: str = DEFAULT_LANGUAGE, session: Session = Depends(get_session)):
    """Universal, jurisdiction-neutral PDF (plan §63) — distinct from the
    country-specific Tax PDF under /api/tax: applies no country's rules."""
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE
    pdf_bytes = render_accountant_pdf(session, language)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=accountant-report.pdf"},
    )


@router.post("/evidence/verify")
async def verify_evidence(file: UploadFile = File(...)):
    """Validate an evidence archive before it is trusted for audit or review."""
    return verify_evidence_archive(await file.read())


@router.post("/evidence/import")
async def import_evidence(file: UploadFile = File(...)):
    """Validate an uploaded archive for review without changing ledger data.

    Evidence archives are audit exports, not full database snapshots. Full
    application recovery belongs to the encrypted backup restore flow.
    """
    result = verify_evidence_archive(await file.read())
    return {
        **result,
        "ready_for_review": bool(result["valid"]),
        "message": (
            "Evidence archive imported for review; no ledger data was changed."
            if result["valid"]
            else "Evidence archive was not imported because verification failed."
        ),
    }
