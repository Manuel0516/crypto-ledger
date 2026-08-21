from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Numeric, and_, cast, or_
from sqlalchemy.orm import Session, selectinload

from app.connectors.base import RawRecord
from app.connectors.manual import ManualConnector
from app.core.assets.registry import get_or_create_asset
from app.core.ledger.overrides import EDITABLE_FIELDS, apply_override, effective_values, restore_automatic_value
from app.core.ledger.service import ingest, refresh_valuations
from app.core.settings import get_or_create_settings, minimum_activity_threshold
from app.core.attachments.service import delete_attachment_file
from app.db.models import Account, Asset, Attachment, Event, EventLink, Fee, Issue, Override, RawEvent, Valuation

from .deps import get_session

router = APIRouter(prefix="/api/events", tags=["events"])
_MANUAL = ManualConnector()

# Fields whose effective value affects pricing — correcting either means the
# existing (non-manually-pinned) valuations are now computed against a stale
# amount/timestamp and must be recomputed (plan §43/§48).
_PRICING_FIELDS = {"primary_amount", "occurred_at"}
_MAX_PAGE_SIZE = 100


def _serialize_fee(fee: Fee) -> dict:
    return {
        "id": fee.id,
        "fee_type": fee.fee_type,
        "asset_symbol": fee.fee_asset.symbol,
        "amount": fee.fee_amount,
        "fee_recipient": fee.fee_recipient,
        "manual": fee.manual,
    }


def _serialize_issue(issue: Issue) -> dict:
    return {
        "id": issue.id,
        "event_id": issue.event_id,
        "severity": issue.severity,
        "title": issue.title,
        "detail": issue.detail,
        "linkable": issue.title == "Possible internal transfer",
        "markable": issue.title == "Possible internal transfer",
    }


def _serialize_summary(
    session: Session,
    event: Event,
    *,
    account_names: set[str] | None = None,
    open_issue_ids: set[int] | None = None,
) -> dict:
    values, modified = effective_values(session, event)
    valuations = {v.quote_currency: v.total_value for v in event.valuations}
    first_fee = event.fees[0] if event.fees else None
    account_names = account_names if account_names is not None else set()
    open_issue_ids = open_issue_ids if open_issue_ids is not None else set()
    return {
        "id": event.id,
        "asset_symbol": event.primary_asset.symbol,
        "network": event.primary_asset.network,
        "secondary_asset_symbol": event.secondary_asset.symbol if event.secondary_asset else None,
        "direction": event.direction,
        "status": event.status,
        "provenance": event.provenance,
        "normalizer_version": event.normalizer_version,
        "source_id": event.raw_event.source_id if event.raw_event else None,
        "source_timezone": event.source_timezone,
        "imported_at": event.raw_event.received_at.isoformat() if event.raw_event else event.created_at.isoformat(),
        "account_id": event.account_id,
        "fee_amount": first_fee.fee_amount if first_fee else None,
        "fee_asset_symbol": first_fee.fee_asset.symbol if first_fee else None,
        "fee_count": len(event.fees),
        "eur_value": valuations.get("EUR"),
        "sek_value": valuations.get("SEK"),
        "modified": modified or any(v.manual_override for v in event.valuations),
        "has_open_issue": event.id in open_issue_ids,
        # A user can explicitly classify a one-sided transfer as internal when
        # its counterpart is outside the ledger. Keep the existing account
        # label inference for backwards compatibility, but expose the stored
        # classification as the durable source of truth.
        "is_internal": event.internal_transfer or (bool(values.get("destination_label")) and values["destination_label"] in account_names),
        "internal_transfer": event.internal_transfer,
        "description": values.get("description"),
        "merchant": values.get("merchant"),
        "tags": _event_tags(values.get("tags_json")),
        "evidence_reference": values.get("evidence_reference"),
        "linked_event_count": len(event.outgoing_links) + len(event.incoming_links),
        **values,
    }


def _event_tags(tags_json: str | None) -> list[str]:
    if not tags_json:
        return []
    try:
        tags = json.loads(tags_json)
    except json.JSONDecodeError:
        return []
    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def _transaction_evidence(event: Event, values: dict[str, str | None]) -> dict:
    """Expose provider transaction metadata without turning sender fees into
    wallet debits on an incoming transfer.

    EVM connectors retain the original gas fields in raw evidence. Newer BSC
    records also carry MegaNode's transaction-detail response. The structured
    fee below is informational for the transaction; ledger fees remain the
    separately stored fees attached only when this account paid them.
    """
    empty = {
        "transaction_fee_amount": None,
        "transaction_fee_asset": None,
        "gas_used": None,
        "gas_price": None,
        "transaction_input": None,
        "transaction_nonce": None,
        "transaction_index": None,
    }
    if not event.raw_event or not values.get("tx_hash"):
        return empty
    try:
        payload = json.loads(event.raw_event.payload_json)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        return empty
    gas_used = payload.get("gasUsed")
    gas_price = payload.get("gasPrice")
    fee_wei = payload.get("_transaction_fee_wei")
    try:
        if fee_wei in (None, "") and gas_used not in (None, "") and gas_price not in (None, ""):
            fee_wei = Decimal(str(gas_used)) * Decimal(str(gas_price))
        fee_amount = format(Decimal(str(fee_wei)) / Decimal(10**18), "f") if fee_wei not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        fee_amount = None
    native_symbol = payload.get("_native_symbol")
    if not native_symbol and payload.get("_network") == "BNB Smart Chain":
        native_symbol = "BNB"
    return {
        "transaction_fee_amount": fee_amount,
        "transaction_fee_asset": native_symbol,
        "gas_used": str(gas_used) if gas_used not in (None, "") else None,
        "gas_price": str(gas_price) if gas_price not in (None, "") else None,
        "transaction_input": payload.get("input") or None,
        "transaction_nonce": payload.get("nonce"),
        "transaction_index": payload.get("transactionIndex"),
    }


def _event_options():
    return (
        selectinload(Event.primary_asset),
        selectinload(Event.secondary_asset),
        selectinload(Event.raw_event),
        selectinload(Event.fees).selectinload(Fee.fee_asset),
        selectinload(Event.valuations),
        selectinload(Event.overrides),
        selectinload(Event.outgoing_links).selectinload(EventLink.linked_event),
        selectinload(Event.incoming_links).selectinload(EventLink.event),
    )


def _parse_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        timestamp, event_id = cursor.rsplit("|", 1)
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")), int(event_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid activity cursor") from None


@router.get("")
def list_events(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=_MAX_PAGE_SIZE),
    page: int | None = Query(None, ge=1),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    asset: str | None = None,
    account_id: int | None = None,
    source: str | None = None,
    event_type: str | None = None,
    network: str | None = None,
    provenance: str | None = None,
    internal: bool | None = None,
    resolved: bool | None = None,
    search: str | None = None,
    under_threshold: bool = False,
    session: Session = Depends(get_session),
):
    """Query the unified ledger server-side; never load the entire history in the browser."""
    settings = get_or_create_settings(session)
    account_names = {name for (name,) in session.query(Account.name).all()}
    open_issue_ids = {eid for (eid,) in session.query(Issue.event_id).filter(Issue.resolved.is_(False), Issue.event_id.isnot(None))}
    query = session.query(Event).join(Event.primary_asset).outerjoin(Event.raw_event)
    # The default view is a presentation filter. The separate under-threshold
    # view exposes the records it excludes. Unpriced events stay visible
    # because hiding an event with missing valuation data would turn a pricing
    # problem into silent data loss.
    minimum_value = minimum_activity_threshold(settings)
    if minimum_value > 0:
        query = (
            query.outerjoin(
                Valuation,
                and_(Valuation.event_id == Event.id, Valuation.quote_currency == settings.minimum_activity_currency),
            )
            .filter(
                and_(Valuation.id.isnot(None), cast(Valuation.total_value, Numeric) < minimum_value)
                if under_threshold
                else or_(Valuation.id.is_(None), cast(Valuation.total_value, Numeric) >= minimum_value)
            )
        )
    elif under_threshold:
        # A zero threshold means nothing is excluded, so the recovery view is
        # intentionally empty rather than showing all activity.
        query = query.filter(Event.id == -1)
    if date_from:
        query = query.filter(Event.occurred_at >= date_from)
    if date_to:
        query = query.filter(Event.occurred_at <= date_to)
    if asset:
        query = query.filter(Asset.symbol == asset.strip().upper())
    if account_id is not None:
        query = query.filter(Event.account_id == account_id)
    if source:
        needle = source.strip()
        query = query.filter(or_(RawEvent.source_id.ilike(f"%{needle}%"), Event.source_label.ilike(f"%{needle}%")))
    if event_type:
        query = query.filter(Event.event_type == event_type.strip().upper())
    if network:
        query = query.filter(Asset.network == network)
    if provenance:
        query = query.filter(Event.provenance == provenance)
    if internal is not None:
        query = query.filter(Event.destination_label.in_(account_names) if internal else or_(Event.destination_label.is_(None), Event.destination_label.notin_(account_names)))
    if resolved is not None:
        issue_event_ids = session.query(Issue.event_id).filter(Issue.resolved.is_(False), Issue.event_id.isnot(None))
        query = query.filter(~Event.id.in_(issue_event_ids) if resolved else Event.id.in_(issue_event_ids))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(
            Event.tx_hash.ilike(pattern), Event.order_id.ilike(pattern), Event.trade_id.ilike(pattern),
            Event.deposit_id.ilike(pattern), Event.withdrawal_id.ilike(pattern), Event.address_from.ilike(pattern),
            Event.address_to.ilike(pattern), Event.counterparty.ilike(pattern), Event.description.ilike(pattern),
            Event.merchant.ilike(pattern), Event.tags_json.ilike(pattern), Event.evidence_reference.ilike(pattern),
            Event.notes.ilike(pattern), Event.source_label.ilike(pattern), Event.destination_label.ilike(pattern),
        ))
    total = query.count()
    parsed_cursor = _parse_cursor(cursor)
    if parsed_cursor:
        cursor_time, cursor_id = parsed_cursor
        query = query.filter(or_(Event.occurred_at < cursor_time, (Event.occurred_at == cursor_time) & (Event.id < cursor_id)))
    events_query = (
        query.options(*_event_options())
        .order_by(Event.occurred_at.desc(), Event.id.desc())
    )
    page_number = page if isinstance(page, int) else None
    if page_number is not None and not parsed_cursor:
        events_query = events_query.offset((page_number - 1) * limit)
    events = events_query.limit(limit + 1).all()
    has_more = len(events) > limit
    items = events[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last.occurred_at.isoformat()}|{last.id}"
    return {
        "items": [_serialize_summary(session, e, account_names=account_names, open_issue_ids=open_issue_ids) for e in items],
        "next_cursor": next_cursor,
        "total": total,
    }


@router.get("/{event_id}")
def get_event(event_id: int, session: Session = Depends(get_session)):
    event = session.query(Event).options(*_event_options()).filter(Event.id == event_id).one_or_none()
    if event is None:
        raise HTTPException(404, "Event not found")
    values, modified = effective_values(session, event)
    account_names = {name for (name,) in session.query(Account.name).all()}
    open_issue_ids = {eid for (eid,) in session.query(Issue.event_id).filter(Issue.resolved.is_(False), Issue.event_id.isnot(None))}
    return {
        "event": _serialize_summary(session, event, account_names=account_names, open_issue_ids=open_issue_ids),
        "valuations": [
            {
                "id": v.id,
                "quote_currency": v.quote_currency,
                "unit_price": v.unit_price,
                "total_value": v.total_value,
                "provider": v.provider,
                "method": v.method,
                "granularity": v.granularity,
                "confidence": v.confidence,
                "manual_override": v.manual_override,
                "requested_timestamp": v.requested_timestamp.isoformat(),
                "observation_timestamp": v.observation_timestamp.isoformat(),
                "fetched_at": v.fetched_at.isoformat(),
            }
            for v in event.valuations
        ],
        "fees": [_serialize_fee(f) for f in event.fees],
        "issues": [
            _serialize_issue(issue)
            for issue in session.query(Issue).filter(Issue.event_id == event.id, Issue.resolved.is_(False)).order_by(Issue.severity, Issue.id).all()
        ],
        "raw": (
            {
                "id": event.raw_event.id,
                "source_id": event.raw_event.source_id,
                "external_id": event.raw_event.external_id,
                "received_at": event.raw_event.received_at.isoformat(),
                "source_timestamp": event.raw_event.source_timestamp.isoformat() if event.raw_event.source_timestamp else None,
                "source_timezone": event.raw_event.source_timezone,
                "source_reference": event.raw_event.source_reference,
                "payload_hash": event.raw_event.payload_hash,
                "connector_version": event.raw_event.connector_version,
                "payload": json.loads(event.raw_event.payload_json),
            }
            if event.raw_event_id and event.raw_event
            else None
        ),
        "evidence": {
            "tx_hash": values["tx_hash"],
            "block_height": event.block_height,
            "block_hash": values["block_hash"],
            "log_index": event.log_index,
            "contract_address": values["contract_address"],
            "order_id": values["order_id"],
            "trade_id": values["trade_id"],
            "deposit_id": values["deposit_id"],
            "withdrawal_id": values["withdrawal_id"],
            **_transaction_evidence(event, values),
        },
        "overrides": [
            {"field": o.field, "old_value": o.old_value, "new_value": o.new_value, "changed_at": o.changed_at.isoformat(), "reason": o.reason}
            for o in sorted(event.overrides, key=lambda o: o.id or 0)
        ],
        "links": _serialize_links(event),
        "original_values": {field: _base_event_value(event, field) for field in EDITABLE_FIELDS},
        "modified": modified or any(v.manual_override for v in event.valuations),
    }


@router.delete("/{event_id}")
def delete_event(event_id: int, session: Session = Depends(get_session)):
    """Permanently remove one activity record and all of its owned data."""
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")

    raw_event_id = event.raw_event_id

    # These tables do not all have an ORM relationship back to Event, so make
    # the ownership boundary explicit instead of leaving orphaned records.
    session.query(Issue).filter(Issue.event_id == event_id).delete(synchronize_session=False)
    session.query(EventLink).filter(
        or_(EventLink.event_id == event_id, EventLink.linked_event_id == event_id)
    ).delete(synchronize_session=False)

    attachments = session.query(Attachment).filter(Attachment.event_id == event_id).all()
    for attachment in attachments:
        delete_attachment_file(attachment)
    session.query(Attachment).filter(Attachment.event_id == event_id).delete(synchronize_session=False)

    # Event relationships use delete-orphan cascades for these rows. Loading
    # the event without _event_options keeps the delete lightweight while the
    # ORM still removes the owned fees, valuations, and overrides.
    session.delete(event)
    session.flush()

    # A connector can produce more than one canonical event from one raw
    # payload. Keep shared evidence until its last event is gone.
    if raw_event_id is not None and not session.query(Event).filter(Event.raw_event_id == raw_event_id).first():
        raw_event = session.get(RawEvent, raw_event_id)
        if raw_event is not None:
            session.delete(raw_event)

    session.commit()
    return {"deleted": event_id}


def _base_event_value(event: Event, field: str) -> str | None:
    value = event.occurred_at.isoformat() if field == "occurred_at" else getattr(event, field)
    return value


def _serialize_links(event: Event) -> list[dict]:
    links: list[dict] = []
    for link in event.outgoing_links:
        links.append(_serialize_link(link, link.linked_event, "outgoing"))
    for link in event.incoming_links:
        links.append(_serialize_link(link, link.event, "incoming"))
    return sorted(links, key=lambda item: item["id"])


def _serialize_link(link: EventLink, related: Event, orientation: str) -> dict:
    return {
        "id": link.id,
        "event_id": related.id,
        "relationship_type": link.relationship_type,
        "orientation": orientation,
        "provenance": link.provenance,
        "confidence": link.confidence,
        "notes": link.notes,
        "created_at": link.created_at.isoformat(),
        "event_type": related.event_type,
        "occurred_at": related.occurred_at.isoformat(),
        "asset_symbol": related.primary_asset.symbol,
        "amount": related.primary_amount,
    }


class ManualEventIn(BaseModel):
    event_type: str = "MANUAL_ADJUSTMENT"
    event_subtype: str | None = None
    symbol: str = "BTC"
    asset_network: str | None = None
    amount: str
    secondary_symbol: str | None = None
    secondary_asset_network: str | None = None
    secondary_amount: str | None = None
    occurred_at: str
    account_id: int | None = None
    source_label: str = "Manual"
    destination_label: str | None = None
    counterparty: str | None = None
    description: str | None = None
    merchant: str | None = None
    tags: list[str] = Field(default_factory=list)
    evidence_reference: str | None = None
    source_timezone: str | None = None
    address_from: str | None = None
    address_to: str | None = None
    notes: str | None = None
    tx_hash: str | None = None
    order_id: str | None = None
    trade_id: str | None = None
    deposit_id: str | None = None
    withdrawal_id: str | None = None
    fee_asset: str | None = None
    fee_amount: str | None = None
    fee_type: str = "NETWORK_FEE"
    eur_value: str | None = None
    sek_value: str | None = None

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_present(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol is required")
        return value

    @field_validator("amount")
    @classmethod
    def amount_must_be_numeric(cls, value: str) -> str:
        try:
            amount = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ValueError("amount must be a decimal number")
        if amount == 0:
            raise ValueError("amount must not be zero")
        return value

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_valid(cls, value: str) -> str:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("occurred_at must be an ISO-8601 timestamp")
        return value

    @field_validator("secondary_symbol")
    @classmethod
    def secondary_symbol_normalized(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    @field_validator("fee_amount", "secondary_amount")
    @classmethod
    def fee_amount_must_be_numeric(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            if Decimal(value) == 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError("amount must be a non-zero decimal number")
        return value

    @field_validator("eur_value", "sek_value")
    @classmethod
    def fiat_value_must_be_numeric(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        try:
            Decimal(value)
        except (InvalidOperation, ValueError):
            raise ValueError("value must be a decimal number")
        return value

    @field_validator("tags")
    @classmethod
    def tags_must_be_clean(cls, value: list[str]) -> list[str]:
        return sorted({tag.strip() for tag in value if tag.strip()})


def _apply_manual_valuation(session: Session, event: Event, currency: str, total_value: str | None) -> None:
    if not total_value:
        return
    amount = abs(Decimal(event.primary_amount))
    if amount == 0:
        return
    total = Decimal(total_value)
    unit_price = total / amount
    _upsert_valuation(session, event, currency, unit_price, total)


def _upsert_valuation(session: Session, event: Event, currency: str, unit_price: Decimal, total_value: Decimal) -> Valuation:
    existing = session.query(Valuation).filter_by(event_id=event.id, quote_currency=currency).one_or_none()
    if existing:
        existing.unit_price = str(unit_price)
        existing.total_value = str(total_value)
        existing.provider = "manual"
        existing.provider_asset_id = event.primary_asset.symbol
        existing.method = "MANUAL"
        existing.granularity = "manual"
        existing.confidence = "manual"
        existing.manual_override = True
        valuation = existing
    else:
        valuation = Valuation(
            event_id=event.id,
            quote_currency=currency,
            unit_price=str(unit_price),
            total_value=str(total_value),
            requested_timestamp=event.occurred_at,
            observation_timestamp=event.occurred_at,
            provider="manual",
            provider_asset_id=event.primary_asset.symbol,
            method="MANUAL",
            granularity="manual",
            confidence="manual",
            manual_override=True,
        )
        session.add(valuation)
    session.query(Issue).filter(
        Issue.event_id == event.id, Issue.title == "Missing price", Issue.resolved.is_(False)
    ).update({Issue.resolved: True})
    return valuation


@router.post("/manual")
def create_manual_event(body: ManualEventIn, session: Session = Depends(get_session)):
    payload = body.model_dump()
    account_id = None
    account = None
    if body.account_id is not None:
        account = session.get(Account, body.account_id)
        if account is None:
            raise HTTPException(404, "Account not found")
        payload["source_label"] = account.name
        account_id = account.id

    if body.event_subtype == "opening_balance":
        if account is None or account.connector_type != "manual":
            raise HTTPException(400, "Opening balances can only be attached to manual linked accounts")
        if Decimal(body.amount) <= 0:
            raise HTTPException(400, "An opening balance must be a positive amount")
        if session.query(Event).filter_by(account_id=account.id, event_subtype="opening_balance").first() is not None:
            raise HTTPException(409, "This manual account already has an opening balance; correct that event from Activity instead")

    external_id = f"{payload['occurred_at']}-{payload['amount']}-{payload['symbol']}-{payload.get('tx_hash') or ''}"
    raw = RawRecord(
        "manual",
        external_id,
        datetime.fromisoformat(payload["occurred_at"]),
        payload,
        source_timezone=body.source_timezone,
        source_reference=body.evidence_reference,
    )
    has_manual_price = bool(body.eur_value or body.sek_value)
    price_currencies: tuple[str, ...] | None = () if has_manual_price else None

    event = ingest(session, _MANUAL, raw, account_id=account_id, price_currencies=price_currencies)
    if event is None:
        raise HTTPException(409, "This manual event was already recorded")

    if has_manual_price:
        _apply_manual_valuation(session, event, "EUR", body.eur_value)
        _apply_manual_valuation(session, event, "SEK", body.sek_value)

    session.commit()
    return {"id": event.id, "status": event.status}


@router.post("/{event_id}/review")
def mark_reviewed(event_id: int, session: Session = Depends(get_session)):
    """Confirms a REQUIRES_REVIEW event has been checked by a person and can
    be treated as settled. Status is workflow state, not evidence, so this
    mutates the row directly rather than going through the override system
    (same precedent as Issue.resolved)."""
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    event.status = "COMPLETE"
    session.commit()
    return _serialize_summary(session, event)


class OverrideIn(BaseModel):
    field: str
    value: str | None
    reason: str | None = None


class RestoreIn(BaseModel):
    reason: str | None = None


class InternalTransferIn(BaseModel):
    enabled: bool = True


@router.patch("/{event_id}")
def override_event(event_id: int, body: OverrideIn, session: Session = Depends(get_session)):
    if body.field not in EDITABLE_FIELDS:
        raise HTTPException(400, f"'{body.field}' is not editable; allowed: {sorted(EDITABLE_FIELDS)}")
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    try:
        apply_override(session, event, body.field, body.value, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if body.field in _PRICING_FIELDS:
        values, _ = effective_values(session, event)
        refresh_valuations(
            session,
            event,
            amount=Decimal(values["primary_amount"]),
            occurred_at=datetime.fromisoformat(values["occurred_at"]),
        )

    session.commit()
    values, modified = effective_values(session, event)
    return {"id": event.id, "values": values, "modified": modified}


@router.post("/{event_id}/overrides/{field}/restore")
def restore_event_value(event_id: int, field: str, body: RestoreIn, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    try:
        restore_automatic_value(session, event, field, body.reason)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if field in _PRICING_FIELDS:
        values, _ = effective_values(session, event)
        refresh_valuations(
            session,
            event,
            amount=Decimal(values["primary_amount"]),
            occurred_at=datetime.fromisoformat(values["occurred_at"]),
        )
    session.commit()
    values, modified = effective_values(session, event)
    return {"id": event.id, "values": values, "modified": modified}


@router.post("/{event_id}/internal-transfer")
def set_internal_transfer(event_id: int, body: InternalTransferIn, session: Session = Depends(get_session)):
    """Explicitly classify one event as an internal move.

    A counterpart EventLink remains optional: this is for transfers where the
    other wallet/account is not connected to this ledger, or its history is no
    longer available from the source. The choice is persisted and therefore
    remains stable even if account names later change.
    """
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    event.internal_transfer = body.enabled
    if body.enabled:
        session.query(Issue).filter(
            Issue.event_id == event_id,
            Issue.title == "Possible internal transfer",
            Issue.resolved.is_(False),
        ).update({Issue.resolved: True}, synchronize_session=False)
    session.commit()
    return {"id": event.id, "internal_transfer": event.internal_transfer}


class EventLinkIn(BaseModel):
    linked_event_id: int
    relationship_type: str = "RELATED"
    confidence: str | None = None
    notes: str | None = None

    @field_validator("relationship_type")
    @classmethod
    def relationship_type_must_be_present(cls, value: str) -> str:
        result = value.strip().upper().replace(" ", "_")
        if not result:
            raise ValueError("relationship type is required")
        return result


@router.post("/{event_id}/links")
def create_event_link(event_id: int, body: EventLinkIn, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    linked = session.get(Event, body.linked_event_id)
    if event is None or linked is None:
        raise HTTPException(404, "Event not found")
    if event.id == linked.id:
        raise HTTPException(400, "An event cannot link to itself")
    existing = session.query(EventLink).filter(
        EventLink.relationship_type == body.relationship_type,
        or_(
            (EventLink.event_id == event.id) & (EventLink.linked_event_id == linked.id),
            (EventLink.event_id == linked.id) & (EventLink.linked_event_id == event.id),
        ),
    ).one_or_none()
    if existing:
        raise HTTPException(409, "These events are already linked with this relationship")
    link = EventLink(
        event_id=event.id,
        linked_event_id=linked.id,
        relationship_type=body.relationship_type,
        provenance="manual",
        confidence=body.confidence,
        notes=body.notes,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return _serialize_link(link, linked, "outgoing")


@router.delete("/{event_id}/links/{link_id}")
def delete_event_link(event_id: int, link_id: int, session: Session = Depends(get_session)):
    link = session.get(EventLink, link_id)
    if link is None or event_id not in {link.event_id, link.linked_event_id}:
        raise HTTPException(404, "Event link not found")
    session.delete(link)
    session.commit()
    return {"deleted": link_id}


class ValuationIn(BaseModel):
    unit_price: str | None = None
    total_value: str | None = None
    reason: str | None = None

    @field_validator("unit_price")
    @classmethod
    def must_be_decimal(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        try:
            if Decimal(value) < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError("unit_price must be a non-negative decimal number")
        return value


@router.put("/{event_id}/valuations/{currency}")
def set_valuation(event_id: int, currency: str, body: ValuationIn, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    currency = currency.upper()
    if body.unit_price is None and body.total_value is None:
        raise HTTPException(400, "Provide unit_price or total_value")
    amount = abs(Decimal(event.primary_amount))
    if amount == 0:
        raise HTTPException(400, "Cannot price an event with zero amount")
    unit_price = Decimal(body.unit_price) if body.unit_price is not None else Decimal(body.total_value) / amount
    total = (amount * unit_price).quantize(Decimal("0.01"))
    previous = session.query(Valuation).filter_by(event_id=event.id, quote_currency=currency).one_or_none()
    valuation = _upsert_valuation(session, event, currency, unit_price, total)
    session.add(Override(
        event_id=event.id,
        field=f"{currency.lower()}_value",
        old_value=previous.total_value if previous else None,
        new_value=str(total),
        reason=body.reason,
    ))
    session.commit()
    return {
        "id": valuation.id,
        "quote_currency": currency,
        "unit_price": str(unit_price),
        "total_value": str(total),
    }


@router.post("/{event_id}/valuations/{currency}/restore")
def restore_valuation(event_id: int, currency: str, body: RestoreIn, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    currency = currency.upper()
    valuation = session.query(Valuation).filter_by(event_id=event.id, quote_currency=currency).one_or_none()
    if valuation is None or not valuation.manual_override:
        raise HTTPException(400, "This valuation is already automatic")
    old_value = valuation.total_value
    session.delete(valuation)
    session.flush()
    refresh_valuations(session, event, currencies=(currency,))
    restored = session.query(Valuation).filter_by(event_id=event.id, quote_currency=currency).one_or_none()
    session.add(Override(
        event_id=event.id,
        field=f"{currency.lower()}_value",
        old_value=old_value,
        new_value=restored.total_value if restored else None,
        reason=body.reason or "Restored automatic valuation",
    ))
    session.commit()
    if restored is None:
        return {"restored": False, "message": "Automatic price is unavailable; a review issue was created"}
    return {"restored": True, "id": restored.id, "total_value": restored.total_value, "method": restored.method}


class FeeIn(BaseModel):
    fee_type: str = "NETWORK_FEE"
    asset_symbol: str
    amount: str
    fee_recipient: str | None = None

    @field_validator("asset_symbol")
    @classmethod
    def symbol_must_be_present(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("asset symbol is required")
        return value

    @field_validator("amount")
    @classmethod
    def amount_must_be_numeric(cls, value: str) -> str:
        try:
            if Decimal(value) <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError("fee amount must be a positive decimal number")
        return value


@router.post("/{event_id}/fees")
def add_fee(event_id: int, body: FeeIn, session: Session = Depends(get_session)):
    event = session.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    fee_asset = get_or_create_asset(session, body.asset_symbol)
    fee = Fee(
        event_id=event.id,
        fee_type=body.fee_type,
        fee_asset_id=fee_asset.id,
        fee_amount=body.amount,
        fee_recipient=body.fee_recipient,
        manual=True,
    )
    session.add(fee)
    session.commit()
    return _serialize_fee(fee)


@router.patch("/{event_id}/fees/{fee_id}")
def edit_fee(event_id: int, fee_id: int, body: FeeIn, session: Session = Depends(get_session)):
    fee = session.get(Fee, fee_id)
    if fee is None or fee.event_id != event_id:
        raise HTTPException(404, "Fee not found")
    fee_asset = get_or_create_asset(session, body.asset_symbol)
    fee.fee_type = body.fee_type
    fee.fee_asset_id = fee_asset.id
    fee.fee_amount = body.amount
    fee.fee_recipient = body.fee_recipient
    session.commit()
    return _serialize_fee(fee)


@router.delete("/{event_id}/fees/{fee_id}")
def delete_fee(event_id: int, fee_id: int, session: Session = Depends(get_session)):
    fee = session.get(Fee, fee_id)
    if fee is None or fee.event_id != event_id:
        raise HTTPException(404, "Fee not found")
    session.delete(fee)
    session.commit()
    return {"deleted": fee_id}
