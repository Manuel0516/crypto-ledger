from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.db.models import BackupRecord

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "data/backups"))


def _db_path() -> Path:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/ledger.db")
    return Path(url.removeprefix("sqlite:///"))


def _get_key() -> bytes:
    key = os.getenv("BACKUP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY is not set; refusing to write an unencrypted backup")
    return key.encode()


def _backup_path(path: str) -> Path:
    """Resolve backup paths across host and container working directories."""
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    fallback = BACKUP_DIR / candidate.name
    return fallback if fallback.exists() else candidate


def create_backup(session: Session) -> BackupRecord:
    """Consistent SQLite snapshot (VACUUM INTO), hashed, then encrypted at
    rest. The encryption key never lives inside the archive or the DB
    (plan §74-75)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.db"
        source = sqlite3.connect(_db_path())
        try:
            source.execute(f"VACUUM INTO '{snapshot_path}'")
        finally:
            source.close()
        plaintext = snapshot_path.read_bytes()

    ciphertext = Fernet(_get_key()).encrypt(plaintext)
    digest = hashlib.sha256(plaintext).hexdigest()

    filename = f"ledger-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.db.enc"
    out_path = BACKUP_DIR / filename
    out_path.write_bytes(ciphertext)

    record = BackupRecord(
        created_at=timestamp,
        path=str(out_path),
        sha256=digest,
        size_bytes=len(plaintext),
        verified=False,
    )
    session.add(record)
    session.flush()
    return record


def _verified_backup_bytes(ciphertext: bytes, expected_sha256: str | None = None) -> bytes:
    try:
        plaintext = Fernet(_get_key()).decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("Backup could not be decrypted") from exc

    if expected_sha256 and hashlib.sha256(plaintext).hexdigest() != expected_sha256:
        raise ValueError("Backup hash does not match the recorded value")

    with tempfile.TemporaryDirectory() as tmp:
        restored_path = Path(tmp) / "restored.db"
        restored_path.write_bytes(plaintext)
        conn = sqlite3.connect(restored_path)
        try:
            status = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if status != "ok":
            raise ValueError(f"Integrity check failed: {status}")

    return plaintext


def _verified_plaintext(record: BackupRecord) -> bytes:
    return _verified_backup_bytes(_backup_path(record.path).read_bytes(), record.sha256)


def verify_backup(session: Session, backup_id: int) -> BackupRecord:
    """Decrypt, re-hash, and run PRAGMA integrity_check against a restored
    temp copy — never trust a backup that hasn't been proven restorable."""
    record = session.get(BackupRecord, backup_id)
    if record is None:
        raise ValueError("Backup not found")

    _verified_plaintext(record)

    record.verified = True
    record.verified_at = datetime.now(timezone.utc)
    session.flush()
    return record


def has_backup_today(session: Session, *, now: datetime | None = None) -> bool:
    today = (now or datetime.now(timezone.utc)).date()
    latest = session.query(BackupRecord).order_by(BackupRecord.created_at.desc()).first()
    return latest is not None and latest.created_at.date() == today


def backup_is_due(session: Session, backup_hour_utc: int, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now.hour >= backup_hour_utc and not has_backup_today(session, now=now)


def list_backups(session: Session) -> list[BackupRecord]:
    return session.query(BackupRecord).order_by(BackupRecord.created_at.desc()).all()


def prune_backups(
    session: Session,
    *,
    daily: int,
    weekly: int,
    monthly: int,
    now: datetime | None = None,
) -> int:
    """Keep recent daily snapshots, then one snapshot per older week/month.

    This only removes snapshots managed by the backup registry and never
    touches the live ledger or any path outside BACKUP_DIR.
    """
    now = now or datetime.now(timezone.utc)
    records = list_backups(session)
    daily_cutoff = now.date() - timedelta(days=daily)
    weekly_cutoff = now.date() - timedelta(weeks=weekly)
    monthly_cutoff = (now.year * 12 + now.month - 1) - monthly
    keep_ids: set[int] = set()
    seen_weeks: set[tuple[int, int]] = set()
    seen_months: set[tuple[int, int]] = set()

    for record in records:
        created = record.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        else:
            created = created.astimezone(timezone.utc)
        if created.date() >= daily_cutoff:
            keep_ids.add(record.id)
            continue
        if created.date() >= weekly_cutoff:
            week = created.isocalendar()
            key = (week.year, week.week)
            if key not in seen_weeks:
                keep_ids.add(record.id)
                seen_weeks.add(key)
            continue
        month_index = created.year * 12 + created.month - 1
        if month_index >= monthly_cutoff:
            key = (created.year, created.month)
            if key not in seen_months:
                keep_ids.add(record.id)
                seen_months.add(key)

    removed = 0
    for record in records:
        if record.id in keep_ids:
            continue
        path = _backup_path(record.path)
        if path.parent.resolve() == BACKUP_DIR.resolve():
            path.unlink(missing_ok=True)
        session.delete(record)
        removed += 1
    session.flush()
    return removed


def backup_bytes(session: Session, backup_id: int) -> tuple[BackupRecord, bytes]:
    record = session.get(BackupRecord, backup_id)
    if record is None:
        raise ValueError("Backup not found")
    return record, _backup_path(record.path).read_bytes()


def import_backup(session: Session, ciphertext: bytes) -> BackupRecord:
    """Validate and register an encrypted backup received from another host."""
    if not ciphertext:
        raise ValueError("Backup file is empty")

    plaintext = _verified_backup_bytes(ciphertext)
    timestamp = datetime.now(timezone.utc)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"ledger-upload-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.db.enc"
    out_path = BACKUP_DIR / filename
    out_path.write_bytes(ciphertext)

    record = BackupRecord(
        created_at=timestamp,
        path=str(out_path),
        sha256=hashlib.sha256(plaintext).hexdigest(),
        size_bytes=len(plaintext),
        verified=True,
        verified_at=timestamp,
    )
    session.add(record)
    session.flush()
    return record


def restore_backup(session: Session, backup_id: int) -> int:
    """Verify a snapshot, then restore it into the live SQLite database."""
    record = session.get(BackupRecord, backup_id)
    if record is None:
        raise ValueError("Backup not found")

    plaintext = _verified_plaintext(record)
    restored_id = record.id
    # Release the read transaction opened by session.get before SQLite writes
    # the verified snapshot into the live database.
    session.rollback()
    with tempfile.TemporaryDirectory() as tmp:
        restored_path = Path(tmp) / "restored.db"
        restored_path.write_bytes(plaintext)
        source = sqlite3.connect(restored_path)
        target = sqlite3.connect(_db_path())
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    session.expire_all()
    return restored_id
