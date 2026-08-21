from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.db.models import Event, Override

# Every field a user can correct after the fact. The base Event row is never
# rewritten for any of these — an Override just shadows it for display/export
# (plan §40's RAW -> NORMALIZED -> USER OVERRIDE -> EFFECTIVE EVENT model).
# Deliberately excluded: the primary asset itself — changing *which* asset an
# event is priced against would silently orphan its existing valuations, and
# that risk isn't worth taking for a rare correction.
_TEXT_FIELDS = {
    "event_type", "event_subtype", "address_from", "address_to",
    "tx_hash", "order_id", "trade_id", "deposit_id", "withdrawal_id",
    "contract_address", "block_hash",
}
_REQUIRED_AMOUNT_FIELDS = {"primary_amount"}
# secondary_amount is optional (no second leg is a valid state, e.g. most
# events aren't swaps) — clearing it is allowed; setting it only makes sense
# once a second leg already exists (see apply_override).
_OPTIONAL_AMOUNT_FIELDS = {"secondary_amount"}
_AMOUNT_FIELDS = _REQUIRED_AMOUNT_FIELDS | _OPTIONAL_AMOUNT_FIELDS
_DATETIME_FIELDS = {"occurred_at"}

EDITABLE_FIELDS = _TEXT_FIELDS | _AMOUNT_FIELDS | _DATETIME_FIELDS


def _base_value(event: Event, field: str) -> str | None:
    if field == "occurred_at":
        return event.occurred_at.isoformat()
    return getattr(event, field)


def _validate(field: str, new_value: str | None) -> str | None:
    if field in _AMOUNT_FIELDS:
        if new_value is None:
            if field in _REQUIRED_AMOUNT_FIELDS:
                raise ValueError("Amount cannot be cleared")
            return None
        try:
            if Decimal(new_value) == 0:
                raise ValueError("Amount must not be zero")
        except InvalidOperation:
            raise ValueError("Amount must be a decimal number") from None
        return new_value
    if field in _DATETIME_FIELDS:
        if new_value is None:
            raise ValueError("Date/time cannot be cleared")
        try:
            parsed = datetime.fromisoformat(new_value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("occurred_at must be an ISO-8601 timestamp") from None
        return parsed.isoformat()
    return new_value


def apply_override(session: Session, event: Event, field: str, new_value: str | None, reason: str | None) -> Override:
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"Field '{field}' is not editable")
    if field == "secondary_amount" and new_value is not None and event.secondary_asset_id is None:
        raise ValueError("This event has no second asset leg — it can only be added by recreating it as a swap")
    new_value = _validate(field, new_value)

    # "Old value" reflects the current *effective* value (base + any prior
    # override), not always the original import — otherwise a second edit's
    # history entry would misleadingly show the very first import's value.
    # This also loads event.overrides into the session's in-memory identity
    # map *before* the new row exists — appending to the relationship below
    # (rather than a bare session.add) keeps that already-loaded collection
    # consistent instead of silently going stale for the rest of the request.
    values, _ = effective_values(session, event)
    old_value = values[field]

    override = Override(field=field, old_value=old_value, new_value=new_value, reason=reason)
    event.overrides.append(override)
    session.flush()
    return override


def restore_automatic_value(session: Session, event: Event, field: str, reason: str | None = None) -> Override:
    """Append an auditable restoration rather than deleting correction history.

    The latest override becomes the normalized/base value. ``effective_values``
    therefore returns the automatic value while the user can still see exactly
    when and why the prior manual value was restored.
    """
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"Field '{field}' is not editable")
    values, _ = effective_values(session, event)
    base_value = _base_value(event, field)
    if values[field] == base_value:
        raise ValueError("This field already uses its automatic value")
    override = Override(
        field=field,
        old_value=values[field],
        new_value=base_value,
        reason=reason or "Restored automatic value",
    )
    event.overrides.append(override)
    session.flush()
    return override


def effective_values(session: Session, event: Event) -> tuple[dict[str, str | None], bool]:
    """Base event fields with the latest override applied per field, plus
    whether anything was overridden at all."""
    latest: dict[str, Override] = {}
    # Sort by id, not changed_at: a freshly-appended-but-unflushed row can
    # carry a tz-aware Python-side default while a row round-tripped through
    # SQLite can come back naive, which makes changed_at comparisons crash.
    # Autoincrement id is monotonic and unambiguous for the same purpose.
    for override in sorted(event.overrides, key=lambda o: o.id or 0):
        latest[override.field] = override
    values = {field: _base_value(event, field) for field in EDITABLE_FIELDS}
    for field, override in latest.items():
        # Price corrections are also recorded in the override audit trail,
        # but are stored on Valuation rather than on Event's effective fields.
        if field in values:
            values[field] = override.new_value
    # A restoration stays in the history but should no longer label the event
    # as manually modified. Price corrections live on Valuation and are added
    # by the API when building the event-level status.
    modified = any(values[field] != _base_value(event, field) for field in values)
    return values, modified
