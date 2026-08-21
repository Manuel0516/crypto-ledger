from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.orm import Session

from app.db.models import Account, Override, PriceObservation, RawEvent, TaxReport


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "record"


def _payload_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def export_evidence_archive(session: Session, report_id: int | None = None) -> bytes:
    """Build a portable archive of the evidence behind the ledger (plan §67):
    raw source payloads + integrity hashes, plus the price observations,
    account registry, and correction history that turn those raw payloads
    into the canonical ledger. If report_id names a generated TaxReport,
    its PDF/CSV are included too — the report's own supporting evidence,
    not a replacement for it (plan §68: retain evidence privately, submit
    only what's required, produce supporting evidence if requested)."""
    raw_events = session.query(RawEvent).order_by(RawEvent.received_at, RawEvent.id).all()
    entries = []
    hash_rows: list[dict[str, str]] = []
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for raw in raw_events:
            filename = f"raw/{raw.id:06d}-{_safe_name(raw.source_id)}-{_safe_name(raw.external_id)}.json"
            payload = json.loads(raw.payload_json)
            content = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
            archive.writestr(filename, content)
            entries.append(
                {
                    "raw_event_id": raw.id,
                    "source_id": raw.source_id,
                    "external_id": raw.external_id,
                    "received_at": raw.received_at.isoformat(),
                    "source_timestamp": raw.source_timestamp.isoformat() if raw.source_timestamp else None,
                    "source_timezone": raw.source_timezone,
                    "source_reference": raw.source_reference,
                    "payload_hash": raw.payload_hash,
                    "connector_version": raw.connector_version,
                    "file": filename,
                    "file_sha256": _file_hash(content),
                }
            )
            hash_rows.append({"raw_event_id": str(raw.id), "file": filename, "payload_sha256": raw.payload_hash, "file_sha256": _file_hash(content)})
        hashes = io.StringIO()
        writer = csv.DictWriter(hashes, fieldnames=["raw_event_id", "file", "payload_sha256", "file_sha256"])
        writer.writeheader()
        writer.writerows(hash_rows)
        archive.writestr("integrity/hashes.csv", hashes.getvalue())

        prices = io.StringIO()
        price_writer = csv.writer(prices)
        price_writer.writerow(["provider", "provider_asset_id", "quote_currency", "observation_date", "observation_timestamp", "unit_price", "method", "granularity", "fetched_at"])
        for obs in session.query(PriceObservation).order_by(PriceObservation.observation_date).all():
            price_writer.writerow([obs.provider, obs.provider_asset_id, obs.quote_currency, obs.observation_date, obs.observation_timestamp.isoformat() if obs.observation_timestamp else "", obs.unit_price, obs.method, obs.granularity, obs.fetched_at.isoformat()])
        archive.writestr("prices/observations.csv", prices.getvalue())

        accounts = io.StringIO()
        account_writer = csv.writer(accounts)
        account_writer.writerow(["id", "name", "kind", "connector_type", "chain_network", "status", "created_at", "archived_at"])
        for account in session.query(Account).order_by(Account.id).all():
            account_writer.writerow([account.id, account.name, account.kind, account.connector_type, account.chain_network or "", account.status, account.created_at.isoformat(), account.archived_at.isoformat() if account.archived_at else ""])
        archive.writestr("accounts/accounts.csv", accounts.getvalue())

        overrides = io.StringIO()
        override_writer = csv.writer(overrides)
        override_writer.writerow(["event_id", "field", "old_value", "new_value", "changed_at", "reason"])
        for override in session.query(Override).order_by(Override.id).all():
            override_writer.writerow([override.event_id, override.field, override.old_value or "", override.new_value or "", override.changed_at.isoformat(), override.reason or ""])
        archive.writestr("overrides/changes.csv", overrides.getvalue())

        if report_id is not None:
            report = session.get(TaxReport, report_id)
            if report is not None:
                report_dir = Path(report.output_dir)
                for name in ("report.pdf", "report.csv"):
                    file_path = report_dir / name
                    if file_path.exists():
                        archive.writestr(f"reports/{name}", file_path.read_bytes())

        manifest = {
            "schema_version": 2,
            "hash_algorithm": "SHA-256",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "records": entries,
        }
        serialized_manifest = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        archive.writestr("integrity/manifest.json", serialized_manifest)
        # Retain the original path for consumers of the first archive format.
        archive.writestr("manifest.json", serialized_manifest)
    return output.getvalue()


def verify_evidence_archive(data: bytes) -> dict[str, object]:
    """Verify every payload and manifest file checksum without importing it."""
    try:
        with ZipFile(io.BytesIO(data), "r") as archive:
            manifest_name = "integrity/manifest.json" if "integrity/manifest.json" in archive.namelist() else "manifest.json"
            manifest = json.loads(archive.read(manifest_name))
            records = manifest.get("records", manifest) if isinstance(manifest, dict) else manifest
            if not isinstance(records, list):
                raise ValueError("manifest records are invalid")
            failures: list[dict[str, str]] = []
            for record in records:
                filename = record.get("file")
                if not filename or filename not in archive.namelist():
                    failures.append({"file": str(filename), "reason": "missing file"})
                    continue
                content = archive.read(filename)
                if record.get("file_sha256") and _file_hash(content) != record["file_sha256"]:
                    failures.append({"file": filename, "reason": "file hash mismatch"})
                    continue
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    failures.append({"file": filename, "reason": "invalid JSON"})
                    continue
                if _payload_hash(payload) != record.get("payload_hash"):
                    failures.append({"file": filename, "reason": "payload hash mismatch"})
            return {"valid": not failures, "records": len(records), "failures": failures}
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return {"valid": False, "records": 0, "failures": [{"file": "archive", "reason": str(exc)}]}
