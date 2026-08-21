from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.reporting.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.core.reporting.ledger_csv import export_ledger_csv
from app.core.reporting.pdf import render_tax_report_pdf
from app.core.reporting.tax_csv import render_tax_csv
from app.core.settings import get_or_create_settings
from app.core.tax.adapter import TaxCalculationResult
from app.core.tax.registry import get_adapter, list_countries
from app.core.tax.rp2_runner.runner import RP2Error
from app.db.models import PriceObservation, TaxReport

from .deps import get_session

router = APIRouter(prefix="/api/tax", tags=["tax"])

TAX_REPORTS_DIR = Path(os.getenv("TAX_REPORTS_DIR", "data/tax_reports"))


def _hash_ledger(session: Session) -> str:
    return hashlib.sha256(export_ledger_csv(session).encode()).hexdigest()


def _hash_price_dataset(session: Session) -> str:
    """SHA-256 over every cached price observation (plan §96's "price
    dataset") — the other half of reproducibility alongside the ledger hash:
    a later price backfill/correction changes this even when the ledger
    itself hasn't changed."""
    rows = (
        session.query(PriceObservation)
        .order_by(PriceObservation.provider, PriceObservation.provider_asset_id, PriceObservation.quote_currency, PriceObservation.observation_date)
        .all()
    )
    serialized = "\n".join(
        f"{r.provider}|{r.provider_asset_id}|{r.quote_currency}|{r.observation_date}|{r.unit_price}|{r.method}" for r in rows
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _asdict(value) -> dict:
    if dataclasses.is_dataclass(value):
        return {k: _asdict(v) for k, v in dataclasses.asdict(value).items()}
    return value


@router.get("/countries")
def countries():
    return list_countries()


@router.get("/languages")
def languages():
    return [{"code": code, "default": code == DEFAULT_LANGUAGE} for code in SUPPORTED_LANGUAGES]


@router.get("/readiness")
def readiness(country: str, year: int, session: Session = Depends(get_session)):
    try:
        adapter = get_adapter(country)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    result = adapter.check_readiness(session, year)
    return {
        "country": result.country,
        "tax_year": result.tax_year,
        "event_count": result.event_count,
        "priced_event_count": result.priced_event_count,
        "missing_price_count": result.missing_price_count,
        "manual_event_count": result.manual_event_count,
        "unresolved_issue_count": result.unresolved_issue_count,
        "ambiguous_transfer_count": result.ambiguous_transfer_count,
        "unsynced_source_count": result.unsynced_source_count,
        "unpriced_fee_count": result.unpriced_fee_count,
        "missing_raw_evidence_count": result.missing_raw_evidence_count,
        "activity_count": result.activity_count,
        "priced_activity_count": result.priced_activity_count,
        "warning_count": result.warning_count,
        "incomplete_activity_count": result.incomplete_activity_count,
        "ready": result.ready,
        "issues": [dataclasses.asdict(i) for i in result.issues],
    }


def _serialize_report(report: TaxReport) -> dict:
    return {
        "id": report.id,
        "country": report.country,
        "tax_year": report.tax_year,
        "method": report.method,
        "language": report.language,
        "generated_at": report.generated_at.isoformat(),
        "adapter_version": report.adapter_version,
        "ledger_snapshot_hash": report.ledger_snapshot_hash,
        "price_dataset_hash": report.price_dataset_hash,
        "output_hashes": json.loads(report.output_hashes_json or "{}"),
        "status": report.status,
        "summary": json.loads(report.summary_json),
        "warnings": json.loads(report.warnings_json),
        "has_rp2_outputs": (TAX_REPORTS_DIR / str(report.id) / "rp2_output").exists(),
    }


class GenerateReportIn(BaseModel):
    country: str
    tax_year: int
    method: str | None = None
    language: str | None = None
    # Retained for clients that still send it. Readiness is informational and
    # this field no longer changes report generation.
    acknowledge_blocking_issues: bool = False


@router.post("/reports")
def generate_report(body: GenerateReportIn, session: Session = Depends(get_session)):
    try:
        adapter = get_adapter(body.country)
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    settings = get_or_create_settings(session)
    taxpayer_name = settings.taxpayer_name or "Taxpayer"
    method = body.method or adapter.default_method
    language = (body.language or settings.default_language or DEFAULT_LANGUAGE).lower()
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE

    work_dir = TAX_REPORTS_DIR / "tmp" / f"{body.country}-{body.tax_year}-{os.getpid()}"
    try:
        result = adapter.calculate(session, body.tax_year, method, taxpayer_name, work_dir)
    except RP2Error as exc:
        raise HTTPException(502, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    report = TaxReport(
        country=adapter.country_code,
        tax_year=body.tax_year,
        method=result.method,
        language=language,
        adapter_version=getattr(adapter, "version", "unknown"),
        ledger_snapshot_hash=_hash_ledger(session),
        price_dataset_hash=_hash_price_dataset(session),
        status="complete",
        summary_json=json.dumps(_serialize_result(result), default=str),
        warnings_json=json.dumps(result.warnings),
        output_dir="pending",
    )
    session.add(report)
    session.flush()

    final_dir = TAX_REPORTS_DIR / str(report.id)
    final_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render_tax_report_pdf(result, taxpayer_name, language)
    csv_text = render_tax_csv(result, language)
    (final_dir / "report.pdf").write_bytes(pdf_bytes)
    (final_dir / "report.csv").write_text(csv_text)
    if result.raw_outputs:
        rp2_dir = final_dir / "rp2_output"
        rp2_dir.mkdir(exist_ok=True)
        for label, path in result.raw_outputs.items():
            shutil.copy(path, rp2_dir / Path(path).name)
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    report.output_dir = str(final_dir)
    report.output_hashes_json = json.dumps(
        {"report.pdf": hashlib.sha256(pdf_bytes).hexdigest(), "report.csv": hashlib.sha256(csv_text.encode()).hexdigest()}
    )
    session.commit()
    return _serialize_report(report)


def _serialize_result(result: TaxCalculationResult) -> dict:
    data = _asdict(result)
    data.pop("raw_outputs", None)
    data["generated_at"] = result.generated_at.isoformat()
    return data


@router.get("/reports")
def list_reports(country: str | None = None, tax_year: int | None = None, session: Session = Depends(get_session)):
    query = session.query(TaxReport)
    if country:
        query = query.filter(TaxReport.country == country.upper())
    if tax_year:
        query = query.filter(TaxReport.tax_year == tax_year)
    return [_serialize_report(r) for r in query.order_by(TaxReport.generated_at.desc()).all()]


def _get_report_or_404(report_id: int, session: Session) -> TaxReport:
    report = session.get(TaxReport, report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    return report


@router.get("/reports/{report_id}")
def get_report(report_id: int, session: Session = Depends(get_session)):
    return _serialize_report(_get_report_or_404(report_id, session))


@router.get("/reports/{report_id}/pdf")
def get_report_pdf(report_id: int, session: Session = Depends(get_session)):
    report = _get_report_or_404(report_id, session)
    path = Path(report.output_dir) / "report.pdf"
    if not path.exists():
        raise HTTPException(404, "PDF not found for this report")
    return FileResponse(path, media_type="application/pdf", filename=f"tax-report-{report.country}-{report.tax_year}.pdf")


@router.get("/reports/{report_id}/tax-csv")
def get_report_csv(report_id: int, session: Session = Depends(get_session)):
    report = _get_report_or_404(report_id, session)
    path = Path(report.output_dir) / "report.csv"
    if not path.exists():
        raise HTTPException(404, "CSV not found for this report")
    return FileResponse(path, media_type="text/csv", filename=f"tax-report-{report.country}-{report.tax_year}.csv")


@router.get("/reports/{report_id}/rp2-outputs.zip")
def get_report_rp2_outputs(report_id: int, session: Session = Depends(get_session)):
    report = _get_report_or_404(report_id, session)
    rp2_dir = Path(report.output_dir) / "rp2_output"
    if not rp2_dir.exists():
        raise HTTPException(404, "No RP2 outputs for this report")
    import io
    from zipfile import ZIP_DEFLATED, ZipFile

    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        for file in rp2_dir.iterdir():
            archive.write(file, file.name)
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=rp2-output-{report.country}-{report.tax_year}.zip"},
    )
