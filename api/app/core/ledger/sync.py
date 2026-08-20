from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.connectors.base import ConnectorUnavailable
from app.connectors.binance import BinanceLiveConnector
from app.connectors.bitcoin import BitcoinAddressConnector, BitcoinXpubConnector, looks_like_extended_key
from app.connectors.bitget import BitgetLiveConnector
from app.connectors.evm import EVMAddressConnector
from app.connectors.lightning import LightningConnector
from app.connectors.monero import MoneroConnector
from app.connectors.solana import SolanaAddressConnector
from app.core.ledger.service import ingest, record_sync
from app.db.models import Account, Issue
from app.security.secrets import decrypt_config

SYNCABLE_TYPES = {
    "bitcoin_address",
    "evm_address",
    "solana_address",
    "monero_rpc",
    "lightning_node",
    "bitget_live",
    "binance_live",
}


@dataclass
class SyncResult:
    status: str  # ok | unavailable | unsupported
    imported: int = 0
    skipped: int = 0
    message: str | None = None


def _build_connector(account: Account):
    if account.connector_type == "bitcoin_address":
        address = account.address or ""
        if looks_like_extended_key(address):
            return BitcoinXpubConnector(address, account.name)
        return BitcoinAddressConnector(address, account.name)
    if account.connector_type == "evm_address":
        return EVMAddressConnector(account.address, account.name, chain=account.chain_network or "ethereum")
    if account.connector_type == "solana_address":
        return SolanaAddressConnector(account.address, account.name)
    if account.connector_type == "monero_rpc":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return MoneroConnector(
            config.get("host", "127.0.0.1"),
            int(config.get("port", 18082)),
            account.name,
            username=config.get("username") or None,
            password=config.get("password") or None,
        )
    if account.connector_type == "lightning_node":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return LightningConnector(config.get("host", ""), config.get("macaroon", ""), account.name)
    if account.connector_type == "bitget_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return BitgetLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), config.get("passphrase", ""), account.name)
    if account.connector_type == "binance_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        symbols = [s.strip().upper() for s in (config.get("symbols") or "").split(",") if s.strip()]
        return BinanceLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), account.name, symbols=symbols)
    return None


def sync_account(session: Session, account: Account, *, backfill: bool = False) -> SyncResult:
    """backfill=True pulls deeper history (first connection); backfill=False
    is the routine incremental check a background scheduler runs on a timer
    — cheap, and safe to repeat because raw evidence storage is idempotent."""
    connector = _build_connector(account)
    if connector is None:
        return SyncResult(
            status="unsupported",
            message="This source doesn't support automatic sync yet — use manual entries or a file import.",
        )

    try:
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
        session.commit()
        return SyncResult(status="ok", imported=imported, skipped=skipped)

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
