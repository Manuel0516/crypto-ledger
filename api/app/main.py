from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import accounts, attachments, backups, events, imports, issues, overview, prices, reports, settings, tax
from app.core.backup.service import backup_is_due, create_backup, prune_backups, verify_backup
from app.core.ledger.sync import SYNCABLE_TYPES, sync_account
from app.core.settings import get_or_create_settings
from app.db.models import Account
from app.db.seed import seed_demo
from app.db.session import SessionLocal

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
_BACKUP_CHECK_INTERVAL_SECONDS = 60 * 60


def _run_backup_once() -> None:
    session = SessionLocal()
    try:
        settings = get_or_create_settings(session)
        if backup_is_due(session, settings.backup_hour_utc):
            record = create_backup(session)
            if settings.backup_verify_after_create:
                verify_backup(session, record.id)
            prune_backups(
                session,
                daily=settings.backup_retention_daily,
                weekly=settings.backup_retention_weekly,
                monthly=settings.backup_retention_monthly,
            )
            session.commit()
    except Exception as exc:  # pragma: no cover - defensive, logged not raised
        session.rollback()
        print(f"[backup] skipped: {exc}", file=sys.stderr)
    finally:
        session.close()


def _run_sync_once() -> int:
    """Returns the configured interval in minutes, so the caller knows how
    long to sleep even if this cycle hit an error early."""
    session = SessionLocal()
    try:
        settings = get_or_create_settings(session)
        interval_minutes = settings.sync_interval_minutes
        if not settings.sync_enabled:
            return interval_minutes
        syncable_accounts = (
            session.query(Account)
            .filter(
                Account.archived_at.is_(None),
                Account.paused.is_(False),
                Account.connector_type.in_(SYNCABLE_TYPES),
                Account.last_sync.isnot(None),
            )
            .all()
        )
        for account in syncable_accounts:
            try:
                sync_account(session, account)
            except Exception as exc:  # pragma: no cover - one bad source must not stop the rest
                session.rollback()
                print(f"[sync] {account.name} failed: {exc}", file=sys.stderr)
        return interval_minutes
    except Exception as exc:  # pragma: no cover
        print(f"[sync] loop error: {exc}", file=sys.stderr)
        return 15
    finally:
        session.close()


async def _backup_loop() -> None:
    """Checks once an hour whether a backup was made today; if not, makes
    one. No cron/APScheduler needed for a single-user self-hosted app.
    Runs off the event loop (asyncio.to_thread) — the actual work is
    blocking file/DB I/O, and running it inline would stall every other
    request (including a graceful shutdown) for the duration."""
    while True:
        await asyncio.to_thread(_run_backup_once)
        await asyncio.sleep(_BACKUP_CHECK_INTERVAL_SECONDS)


async def _sync_loop() -> None:
    """Keeps every connected, syncable source up to date on a timer — the
    'real-time' checking layer. Each connected account is re-checked once
    per configured interval (Settings → Synchronization); a single slow or
    failing source never blocks the others, and a source that's never been
    synced gets skipped here (its initial pull happens via the explicit
    Backfill action right after it's connected). Runs off the event loop
    for the same reason as the backup loop — connector calls are blocking
    HTTP requests, sometimes with retry/backoff sleeps, and must never
    freeze the API for everyone else while one source is slow."""
    while True:
        interval_minutes = await asyncio.to_thread(_run_sync_once)
        await asyncio.sleep(max(interval_minutes, 1) * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is Alembic-owned — run `alembic upgrade head` before starting
    # (the Dockerfile CMD does this automatically).
    if DEMO_MODE:
        session = SessionLocal()
        try:
            seed_demo(session)
        finally:
            session.close()
    backup_task = asyncio.create_task(_backup_loop())
    sync_task = asyncio.create_task(_sync_loop())
    try:
        yield
    finally:
        backup_task.cancel()
        sync_task.cancel()


app = FastAPI(title="Crypto Ledger API", version="0.2.0-beta.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for router_module in (overview, accounts, events, issues, reports, imports, backups, settings, tax, attachments, prices):
    app.include_router(router_module.router)
app.include_router(reports.export_router)
