from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.connectors.base import Connector, RawRecord
from app.core.assets.registry import get_or_create_asset
from app.core.pricing.cache import get_historical_prices
from app.core.pricing.provider import HistoricalPrice
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


def _refresh_existing_automatic_event(session: Session, raw: RawRecord, connector: Connector) -> None:
    """Apply newly available source evidence to an existing automatic event.

    Connector improvements must be able to enrich a previously imported
    event on the next backfill without creating a duplicate transaction. The
    base event values are only filled when they were previously absent; any
    user override remains untouched. Raw evidence is replaced only when the
    connector supplied a different payload, and remains the source payload,
    not a hand-edited interpretation.
    """
    existing_raw = (
        session.query(RawEvent)
        .filter_by(source_id=raw.source_id, external_id=raw.external_id)
        .one_or_none()
    )
    if existing_raw is None:
        return
    new_payload_json = json.dumps(raw.payload, sort_keys=True, default=str)
    new_hash = _hash_payload(raw.payload)
    if existing_raw.payload_hash == new_hash:
        return
    event = session.query(Event).filter(Event.raw_event_id == existing_raw.id).one_or_none()
    if event is None or event.provenance != "automatic":
        return

    normalized = connector.normalize(raw)
    for field in (
        "address_from",
        "address_to",
        "tx_hash",
        "block_height",
        "block_hash",
        "log_index",
        "contract_address",
    ):
        if getattr(event, field) in (None, "") and getattr(normalized, field) not in (None, ""):
            setattr(event, field, getattr(normalized, field))
    if not event.fees and normalized.fees:
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
    existing_raw.payload_json = new_payload_json
    existing_raw.payload_hash = new_hash
    existing_raw.connector_version = connector.version
    existing_raw.source_timestamp = raw.source_timestamp
    existing_raw.source_timezone = raw.source_timezone
    event.normalizer_version = connector.version


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
        _refresh_existing_automatic_event(session, raw, connector)
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
    amount = abs(amount)
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

    # A trade or C2C/card transaction quoted directly in one of the ledger's
    # valuation currencies has stronger evidence than a third-party daily
    # market reference: the exchange itself states the exact amount paid or
    # received.  Preserve that unit price first (Project overview §45), then
    # only ask the price provider for currencies the execution did not quote.
    # This also prices a newly seen coin bought for EUR/SEK even when it has
    # no CoinGecko symbol mapping yet.
    quoted_asset = event.secondary_asset
    exact_currencies: set[str] = set()
    if quoted_asset is not None and quoted_asset.asset_type == "FIAT" and event.secondary_amount not in (None, ""):
        try:
            primary_amount = abs(Decimal(event.primary_amount))
            quote_amount = abs(Decimal(event.secondary_amount))
        except Exception:
            primary_amount = Decimal(0)
            quote_amount = Decimal(0)
        quote_currency = quoted_asset.symbol.upper()
        if primary_amount and quote_amount and quote_currency in pending:
            _upsert_automatic_valuation(
                event,
                quote_currency,
                unit_price=quote_amount / primary_amount,
                total_value=quote_amount,
                provider="exchange_execution",
                provider_asset_id=quote_currency,
                method="EXACT_EXECUTION",
                granularity="exact",
                confidence="high",
                occurred_at=occurred_at,
                observation_timestamp=occurred_at,
            )
            exact_currencies.add(quote_currency)

    pending = [currency for currency in pending if currency not in exact_currencies]
    if not pending:
        session.query(Issue).filter(
            Issue.event_id == event.id, Issue.title == "Missing price", Issue.resolved.is_(False)
        ).update({Issue.resolved: True})
        return

    provider = configured_price_provider(session)

    # Most exchange trades quote one crypto asset in another (BTC/USDT,
    # ETH/BTC, ...). Their exact execution ratio is stronger evidence than
    # a market price for the primary asset. Cross only the quote asset into
    # each configured fiat currency, retaining both the execution evidence
    # and the actual precision of that FX observation.
    if (
        event.event_type in {"BUY", "SELL", "SWAP"}
        and quoted_asset is not None
        and quoted_asset.asset_type != "FIAT"
        and event.secondary_amount not in (None, "")
    ):
        try:
            primary_amount = abs(Decimal(event.primary_amount))
            quote_amount = abs(Decimal(event.secondary_amount))
        except Exception:
            primary_amount = Decimal(0)
            quote_amount = Decimal(0)
        quote_provider_id = _resolve_provider_asset_id(quoted_asset, provider)
        if primary_amount and quote_amount and quote_provider_id:
            try:
                quote_prices = get_historical_prices(session, provider, quote_provider_id, occurred_at, pending)
            except Exception:
                quote_prices = {}
            resolved_from_execution: set[str] = set()
            for currency in pending:
                quote = quote_prices.get(currency)
                if quote is None:
                    continue
                if isinstance(quote, HistoricalPrice):
                    quote_unit_price, quote_method = quote
                    observation_timestamp = quote.observation_timestamp
                    granularity = f"execution×{quote.granularity}"
                else:  # backwards-compatible provider/test-double result
                    quote_unit_price, quote_method = quote
                    observation_timestamp = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0)
                    granularity = "execution×day"
                total_value = quote_amount * quote_unit_price
                _upsert_automatic_valuation(
                    event,
                    currency,
                    unit_price=total_value / primary_amount,
                    total_value=total_value,
                    provider=f"exchange_execution+{provider.name}",
                    provider_asset_id=f"{asset.symbol}/{quoted_asset.symbol}; fx:{quote_provider_id}; {quote_method}",
                    method="DERIVED_FX",
                    granularity=granularity,
                    confidence="high" if quote_method in {"NEAREST_5_MIN", "NEAREST_HOUR"} else "medium",
                    occurred_at=occurred_at,
                    observation_timestamp=observation_timestamp,
                )
                resolved_from_execution.add(currency)
            pending = [currency for currency in pending if currency not in resolved_from_execution]

    if not pending:
        session.query(Issue).filter(
            Issue.event_id == event.id, Issue.title == "Missing price", Issue.resolved.is_(False)
        ).update({Issue.resolved: True})
        return

    provider_asset_id = _resolve_provider_asset_id(asset, provider)
    if not provider_asset_id:
        _flag_issue(session, event, "Unknown asset — no price source",
                    f"'{asset.symbol}' isn't mapped to a market data provider yet, so no valuation could be attached.")
        return
    session.query(Issue).filter(
        Issue.event_id == event.id, Issue.title == "Unknown asset — no price source", Issue.resolved.is_(False)
    ).update({Issue.resolved: True})

    try:
        prices = get_historical_prices(session, provider, provider_asset_id, occurred_at, pending)
    except Exception:
        prices = {}

    for currency in pending:
        if currency not in prices:
            _flag_issue(session, event, "Missing price", f"No {currency} price could be resolved for this event yet.")
            continue
        quote = prices[currency]
        if isinstance(quote, HistoricalPrice):
            unit_price, method = quote
            observation_timestamp = quote.observation_timestamp
            granularity = quote.granularity
        else:  # backwards-compatible provider/test-double result
            unit_price, method = quote
            observation_timestamp = occurred_at.replace(hour=0, minute=0, second=0, microsecond=0)
            granularity = "day"
        total_value = str((amount * unit_price).quantize(Decimal("0.01")))
        _upsert_automatic_valuation(
            event,
            currency,
            unit_price=unit_price,
            total_value=Decimal(total_value),
            provider=provider.name,
            provider_asset_id=provider_asset_id,
            method=method,
            granularity=granularity,
            confidence="medium",
            occurred_at=occurred_at,
            observation_timestamp=observation_timestamp,
        )
        session.query(Issue).filter(
            Issue.event_id == event.id, Issue.title == "Missing price", Issue.resolved.is_(False)
        ).update({Issue.resolved: True})


def _resolve_provider_asset_id(asset: Asset, provider) -> str | None:
    """Return an asset's persistent market-data identifier, resolving a
    previously unknown ticker only when the active provider offers that
    capability. The caller decides whether failure should become an Issue:
    an exact exchange execution can still value an otherwise unmapped coin
    through its mapped quote asset."""
    if asset.coingecko_id:
        return asset.coingecko_id
    resolve_symbol = getattr(provider, "resolve_symbol", None)
    if resolve_symbol is None:
        return None
    try:
        resolved_id = resolve_symbol(asset.symbol)
    except Exception:
        return None
    if resolved_id:
        asset.coingecko_id = resolved_id
        return resolved_id
    return None


def _upsert_automatic_valuation(
    event: Event,
    currency: str,
    *,
    unit_price: Decimal,
    total_value: Decimal,
    provider: str,
    provider_asset_id: str | None,
    method: str,
    granularity: str,
    confidence: str,
    occurred_at: datetime,
    observation_timestamp: datetime,
) -> None:
    """Create or refresh an automatic valuation without touching a manual
    override.  ``refresh_valuations`` has already excluded manually priced
    currencies before calling this helper."""
    existing = next((v for v in event.valuations if v.quote_currency == currency), None)
    if existing:
        existing.unit_price = str(unit_price)
        existing.total_value = str(total_value.quantize(Decimal("0.01")))
        existing.requested_timestamp = occurred_at
        existing.observation_timestamp = observation_timestamp
        existing.provider = provider
        existing.provider_asset_id = provider_asset_id
        existing.method = method
        existing.granularity = granularity
        existing.fetched_at = datetime.now(timezone.utc)
        existing.confidence = confidence
        return

    # Attach through the relationship as well as the event ID.  The
    # relationship is read above when collecting manual overrides; appending
    # keeps the in-memory Activity response current in the same request.
    event.valuations.append(
        Valuation(
            quote_currency=currency,
            unit_price=str(unit_price),
            total_value=str(total_value.quantize(Decimal("0.01"))),
            requested_timestamp=occurred_at,
            observation_timestamp=observation_timestamp,
            provider=provider,
            provider_asset_id=provider_asset_id,
            method=method,
            granularity=granularity,
            confidence=confidence,
        )
    )


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
