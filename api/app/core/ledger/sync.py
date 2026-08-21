from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.base import ConnectorUnavailable
from app.core.ledger.connectors import build_connector
from app.core.ledger.reconcile import ReconcileResult, reconcile_account
from app.core.ledger.service import ingest, record_sync
from app.db.models import Account, Issue

SYNCABLE_TYPES = {
    "bitcoin_address",
    "evm_address",
    "solana_address",
    "monero_rpc",
    "lightning_node",
    "lightning_nwc",
    "bitget_live",
    "binance_live",
}

_BALANCE_MISMATCH_TITLE = "{name} balance doesn't match the live source"
_SPEND_PERMISSION_TITLE = "{name} connection grants more than read access"


@dataclass
class SyncResult:
    status: str  # ok | unavailable | unsupported
    imported: int = 0
    skipped: int = 0
    message: str | None = None
    reconciliation: ReconcileResult | None = None


def _reconcile_and_flag(session: Session, account: Account, connector) -> ReconcileResult | None:
    """Runs a balance check right after a successful sync, for any source
    that supports one (see fetch_balances on the connector) — every sync,
    not just a manual one-off, so drift gets caught by the scheduler too.
    Best-effort: a reconciliation problem (network hiccup, an unsupported
    source) must never fail the sync that already succeeded."""
    if getattr(connector, "fetch_balances", None) is None:
        return None
    try:
        result = reconcile_account(session, account, connector=connector)
    except Exception:
        return None
    if result.status != "ok":
        return result

    title = _BALANCE_MISMATCH_TITLE.format(name=account.name)
    existing = session.query(Issue).filter_by(title=title, resolved=False).first()
    mismatches = [a for a in result.assets if not a.matches]
    if not mismatches:
        if existing is not None:
            existing.resolved = True
        return result

    detail = "; ".join(f"{a.asset_symbol}: live {a.live_amount}, ledger {a.computed_amount}" for a in mismatches)
    if existing is not None:
        existing.detail = detail
        existing.created_at = datetime.now(timezone.utc)
    else:
        session.add(Issue(event_id=None, severity="warning", title=title, detail=detail))
    return result


def _check_permissions_and_flag(session: Session, account: Account, connector) -> None:
    """For a connector that can report its own actual permissions (NWC's
    get_info) — never used to decide whether to call anything (this app
    never calls a spend-capable method regardless of what's granted), only
    to tell the user their connection grants more than this app needs.
    Best-effort and non-blocking, same shape as _reconcile_and_flag."""
    permissions_fn = getattr(connector, "permissions", None)
    if permissions_fn is None:
        return
    try:
        permissions = permissions_fn()
    except Exception:
        return

    title = _SPEND_PERMISSION_TITLE.format(name=account.name)
    existing = session.query(Issue).filter_by(title=title, resolved=False).first()
    if not permissions.has_spend_capability:
        if existing is not None:
            existing.resolved = True
        return

    detail = (
        f"This connection grants {', '.join(permissions.extra_methods)} in addition to read access. "
        "This app never uses payment-capable permissions, but you may want to reconnect with a "
        "read-only NWC connection if your wallet supports issuing one."
    )
    if existing is not None:
        existing.detail = detail
        existing.created_at = datetime.now(timezone.utc)
    else:
        session.add(Issue(event_id=None, severity="warning", title=title, detail=detail))


def sync_account(session: Session, account: Account, *, backfill: bool = False) -> SyncResult:
    """backfill=True pulls deeper history (first connection); backfill=False
    is the routine incremental check a background scheduler runs on a timer
    — cheap, and safe to repeat because raw evidence storage is idempotent."""
    try:
        connector = build_connector(account, session)
        if connector is None:
            return SyncResult(
                status="unsupported",
                message="This source doesn't support automatic sync yet — use manual entries or a file import.",
            )

        since = None if backfill else account.last_sync
        imported = 0
        skipped = 0
        try:
            for raw in connector.fetch(since=since):
                event = ingest(session, connector, raw, account_id=account.id)
                if event is None:
                    skipped += 1
                else:
                    imported += 1
        except ConnectorUnavailable as exc:
            # Keep whatever was already fetched before the failure — evidence
            # already retrieved must never be discarded just because a later
            # page/call failed (plan §48).
            account.status = "error"
            record_sync(session, connector.source_id, imported, status="partial")
            session.add(Issue(event_id=None, severity="warning", title=f"{account.name} sync stopped early", detail=str(exc)))
            session.commit()
            return SyncResult(status="unavailable", imported=imported, skipped=skipped, message=str(exc))

        account.status = "connected"
        account.last_sync = datetime.now(timezone.utc)
        record_sync(session, connector.source_id, imported)
        reconciliation = _reconcile_and_flag(session, account, connector)
        _check_permissions_and_flag(session, account, connector)
        # A hard, source-imposed history cutoff (e.g. Bitget's UTA API only
        # exposing 90 days) is worth surfacing right after the deep pull
        # that would otherwise hit it — not on every routine incremental
        # sync, where it'd just be repeated noise.
        message = getattr(connector, "history_limit_note", None) if backfill else None
        session.commit()
        return SyncResult(status="ok", imported=imported, skipped=skipped, message=message, reconciliation=reconciliation)

    except ConnectorUnavailable as exc:
        account.status = "error"
        session.add(
            Issue(
                event_id=None,
                severity="warning",
                title=f"{account.name} is not reachable",
                detail=str(exc),
            )
        )
        session.commit()
        return SyncResult(status="unavailable", message=str(exc))

    except Exception as exc:
        # Connector code handles expected provider failures as
        # ConnectorUnavailable. Keep a malformed response or an unexpected
        # provider/schema change from becoming a useless HTTP 500: preserve
        # the account error state and return a message the UI can show and the
        # user can act on or retry.
        session.rollback()
        account = session.get(Account, account.id) or account
        account.status = "error"
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        session.add(
            Issue(
                event_id=None,
                severity="warning",
                title=f"{account.name} sync failed unexpectedly",
                detail=detail,
            )
        )
        session.commit()
        return SyncResult(status="unavailable", message=f"Sync failed unexpectedly: {detail}")
