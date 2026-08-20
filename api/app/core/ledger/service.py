from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.connectors.base import Connector, RawRecord
from app.core.assets.registry import get_or_create_asset
from app.core.pricing.cache import get_historical_prices
from app.core.pricing.config import configured_price_provider
from app.core.reconciliation.matcher import match_transfers
from app.core.ledger.taxonomy import canonicalize_event_type
from app.core.settings import valuation_currencies, get_or_create_settings
from app.db.models import Account, Asset, Event, Fee, Issue, RawEvent, SyncState, Valuation

def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def store_raw(session: Session, raw: RawRecord, connector_version: str) -> RawEvent | None:
    """Idempotent insert of immutable evidence. Returns None if this
    (source, external_id) pair was already recorded — re-syncing a source
    must never create duplicates (plan §71)."""
    existing = (
        session.query(RawEvent)
        .filter_by(source_id=raw.source_id, external_id=raw.external_id)
        .one_or_none()
    )
    if existing:
        return None
    record = RawEvent(
        source_id=raw.source_id,
        external_id=raw.external_id,
        source_timestamp=raw.source_timestamp,
        source_timezone=raw.source_timezone,
        source_reference=raw.source_reference,
        payload_json=json.dumps(raw.payload, sort_keys=True, default=str),
        payload_hash=_hash_payload(raw.payload),
        connector_version=connector_version,
    )
    session.add(record)
    session.flush()
    return record


def ingest(
    session: Session,
    connector: Connector,
    raw: RawRecord,
    *,
    account_id: int | None = None,
    price_currencies: tuple[str, ...] | None = None,
) -> Event | None:
    """Raw evidence -> normalize -> canonical event -> valuations -> reconciliation.
    Returns None if this record was already ingested (idempotent no-op)."""
    raw_row = store_raw(session, raw, connector.version)
    if raw_row is None:
        return None

    normalized = connector.normalize(raw)
    if account_id is None:
        account = session.query(Account).filter(Account.name == normalized.source_label).one_or_none()
        account_id = account.id if account else None
    asset = get_or_create_asset(
        session,
        normalized.asset_symbol,
        network=normalized.asset_network,
        contract_address=normalized.asset_contract,
        asset_type=normalized.asset_type,
    )
    secondary_asset = (
        get_or_create_asset(session, normalized.secondary_asset_symbol, network=normalized.secondary_asset_network)
        if normalized.secondary_asset_symbol
        else None
    )

    event_type, event_subtype = canonicalize_event_type(normalized.event_type, normalized.event_subtype)
    event = Event(
        external_id=f"{raw.source_id}:{raw.external_id}",
        raw_event_id=raw_row.id,
        account_id=account_id,
        event_type=event_type,
        event_subtype=event_subtype,
        direction=normalized.direction,
        status=normalized.status,
        occurred_at=normalized.occurred_at,
        original_timestamp=normalized.original_timestamp,
        source_timezone=normalized.source_timezone or raw.source_timezone,
        primary_asset_id=asset.id,
        primary_amount=normalized.amount,
        secondary_asset_id=secondary_asset.id if secondary_asset else None,
        secondary_amount=normalized.secondary_amount,
        source_label=normalized.source_label,
        destination_label=normalized.destination_label,
        counterparty=normalized.counterparty,
        description=normalized.description,
        merchant=normalized.merchant,
        tags_json=json.dumps(sorted({tag.strip() for tag in normalized.tags if tag.strip()})) if normalized.tags else None,
        evidence_reference=normalized.evidence_reference or raw.source_reference,
        notes=normalized.notes,
        address_from=normalized.address_from,
        address_to=normalized.address_to,
        tx_hash=normalized.tx_hash,
        block_height=normalized.block_height,
        block_hash=normalized.block_hash,
        log_index=normalized.log_index,
        contract_address=normalized.contract_address,
        order_id=normalized.order_id,
        trade_id=normalized.trade_id,
        deposit_id=normalized.deposit_id,
        withdrawal_id=normalized.withdrawal_id,
        provenance="manual" if connector.source_id == "manual" else "automatic",
        normalizer_version=connector.version,
    )
    session.add(event)
    session.flush()

    for fee in normalized.fees:
        fee_asset = get_or_create_asset(session, fee.asset_symbol)
        session.add(
            Fee(
                event_id=event.id,
                fee_type=fee.fee_type,
                fee_asset_id=fee_asset.id,
                fee_amount=fee.amount,
                fee_recipient=fee.fee_recipient,
            )
        )

    _attach_valuations(session, event, asset, price_currencies or valuation_currencies(get_or_create_settings(session)))
    match_transfers(session, event)
    return event


def refresh_valuations(
    session: Session,
    event: Event,
    *,
    amount: Decimal | None = None,
    occurred_at: datetime | None = None,
    currencies: tuple[str, ...] | None = None,
) -> None:
    """Re-price an event against its (possibly just-corrected) amount and/or
    timestamp. A currency the user has manually priced (Valuation.manual_override)
    is left untouched — a manual correction to the amount shouldn't silently
    clobber a manual correction to the price. Existing "Missing price" issues
    for a currency that now resolves are marked resolved."""
    currencies = currencies or valuation_currencies(get_or_create_settings(session))
    amount = amount if amount is not None else Decimal(event.primary_amount)
    occurred_at = occurred_at or event.occurred_at
    asset = event.primary_asset
    if asset.asset_type == "FIAT" or not currencies:
        return

    manually_priced = {
        v.quote_currency for v in event.valuations if v.manual_override and v.quote_currency in currencies
    }
    pending = [c for c in currencies if c not in manually_priced]
    if not pending:
        return

    if not asset.coingecko_id:
        _flag_issue(session, event, "Unknown asset — no price source",
                     f"'{asset.symbol}' isn't mapped to a market data provider yet, so no valuation could be attached.")
        return
    try:
        provider = configured_price_provider(session)
        prices = get_historical_prices(session, provider, asset.coingecko_id, occurred_at, pending)
    except Exception:
        prices = {}

    existing_by_currency = {v.quote_currency: v for v in event.valuations}
    for currency in pending:
        if currency not in prices:
            _flag_issue(session, event, "Missing price", f"No {currency} price could be resolved for this event yet.")
            continue
        unit_price, method = prices[currency]
        total_value = str((amount * unit_price).quantize(Decimal("0.01")))
        existing = existing_by_currency.get(currency)
        if existing:
            existing.unit_price = str(unit_price)
            existing.total_value = total_value
            existing.requested_timestamp = occurred_at
            existing.observation_timestamp = occurred_at
            existing.provider = provider.name
            existing.provider_asset_id = asset.coingecko_id
            existing.method = method
            existing.confidence = "medium"
        else:
            session.add(
                Valuation(
                    event_id=event.id,
                    quote_currency=currency,
                    unit_price=str(unit_price),
                    total_value=total_value,
                    requested_timestamp=occurred_at,
                    observation_timestamp=occurred_at,
                    provider=provider.name,
                    provider_asset_id=asset.coingecko_id,
                    method=method,
                    confidence="medium",
                )
            )
        session.query(Issue).filter(
            Issue.event_id == event.id, Issue.title == "Missing price", Issue.resolved.is_(False)
        ).update({Issue.resolved: True})


def _flag_issue(session: Session, event: Event, title: str, detail: str) -> None:
    already = session.query(Issue).filter_by(event_id=event.id, title=title, resolved=False).one_or_none()
    if already:
        return
    session.add(Issue(event_id=event.id, severity="warning", title=title, detail=detail))


def _attach_valuations(session: Session, event: Event, asset: Asset, currencies: tuple[str, ...]) -> None:
    refresh_valuations(session, event, amount=Decimal(event.primary_amount), occurred_at=event.occurred_at, currencies=currencies)


def record_sync(session: Session, source_id: str, imported: int, status: str = "ok") -> None:
    state = session.get(SyncState, source_id)
    now = datetime.now(timezone.utc)
    if state:
        state.last_sync = now
        state.status = status
        state.records_imported += imported
    else:
        session.add(SyncState(source_id=source_id, last_sync=now, status=status, records_imported=imported))
