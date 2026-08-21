from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session, selectinload

from app.core.ledger.overrides import effective_values
from app.core.settings import get_or_create_settings, is_under_activity_threshold
from app.db.models import Account, Event, Issue, Override, PriceObservation

from .adapter import CorrectionRow, EventScheduleRow, ReadinessIssue, ReconciliationSummary, TaxReadinessResult, TransferRow
from .suggestions import suggest_reclassification

INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
# Same-asset relocation types: correct tax treatment hinges entirely on
# whether the other side is one of your own accounts (non-taxable move) or
# someone else's (a disposal/acquisition) — never guessed, always either
# linked via INTERNAL_TRANSFER or flagged as ambiguous.
_MOVE_TYPES = ("WITHDRAWAL", "DEPOSIT", "TRANSFER", "SEND", "RECEIVE", "BRIDGE_OUT", "BRIDGE_IN")


class EffectiveEvent:
    """An Event with its override-corrected values applied. Every tax
    calculation reads through this, never the raw Event, for the fields a
    user can correct (plan §40) — otherwise a correction made via the
    event's own "Manual correction" panel would silently have zero effect
    on the report calculated from it. Fields that aren't user-editable
    (id, direction, status, provenance, fees, valuations, relationships,
    ...) pass straight through to the underlying Event."""

    __slots__ = ("_event", "_values")

    def __init__(self, event: Event, values: dict[str, str | None]) -> None:
        self._event = event
        self._values = values

    def __getattr__(self, name: str):
        return getattr(self._event, name)

    @property
    def event_type(self) -> str:
        return self._values["event_type"] or self._event.event_type

    @property
    def event_subtype(self) -> str | None:
        return self._values["event_subtype"]

    @property
    def primary_amount(self) -> str:
        return self._values["primary_amount"] or self._event.primary_amount

    @property
    def secondary_amount(self) -> str | None:
        return self._values["secondary_amount"]

    @property
    def occurred_at(self) -> datetime:
        value = self._values["occurred_at"]
        parsed = datetime.fromisoformat(value) if value else self._event.occurred_at
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @property
    def source_label(self) -> str:
        return self._values["source_label"] or self._event.source_label

    @property
    def destination_label(self) -> str | None:
        return self._values["destination_label"]

    @property
    def counterparty(self) -> str | None:
        return self._values["counterparty"]

    @property
    def address_from(self) -> str | None:
        return self._values["address_from"]

    @property
    def address_to(self) -> str | None:
        return self._values["address_to"]

    @property
    def notes(self) -> str | None:
        return self._values["notes"]


def load_events_through(session: Session, tax_year: int) -> list[EffectiveEvent]:
    """All events up to and including 31 Dec of tax_year, oldest first, with
    overrides already applied. Cost-basis methods (FIFO, average-cost) both
    need the full prior history to know what basis is carried into the
    target year — not just that year's events."""
    cutoff = datetime(tax_year + 1, 1, 1, tzinfo=timezone.utc)
    events = (
        session.query(Event)
        .filter(Event.occurred_at < cutoff)
        .options(
            selectinload(Event.primary_asset),
            selectinload(Event.secondary_asset),
            selectinload(Event.fees),
            selectinload(Event.valuations),
            selectinload(Event.outgoing_links),
            selectinload(Event.incoming_links),
            selectinload(Event.overrides),
        )
        .order_by(Event.occurred_at, Event.id)
        .all()
    )
    settings = get_or_create_settings(session)
    included: list[EffectiveEvent] = []
    for event in events:
        effective = EffectiveEvent(event, effective_values(session, event)[0])
        if not is_under_activity_threshold(effective, settings):
            included.append(effective)
    return included


@dataclass
class TransferPair:
    withdrawal: EffectiveEvent
    deposit: EffectiveEvent


def _linked_ids(event: EffectiveEvent) -> list[int]:
    ids = [link.linked_event_id for link in event.outgoing_links if link.relationship_type == INTERNAL_TRANSFER]
    ids += [link.event_id for link in event.incoming_links if link.relationship_type == INTERNAL_TRANSFER]
    return ids


def classify_moves(events: list[EffectiveEvent]) -> tuple[list[TransferPair], list[EffectiveEvent]]:
    """Splits same-asset move events (WITHDRAWAL/DEPOSIT/TRANSFER/SEND/
    RECEIVE/BRIDGE_OUT/BRIDGE_IN) into confirmed internal-transfer pairs
    (linked via the INTERNAL_TRANSFER relationship, matched by direction
    rather than exact type name) and ambiguous events with no such link.
    Ambiguous events are never guessed at — they block report generation
    instead (plan §95)."""
    by_id = {e.id: e for e in events}
    moves = [e for e in events if e.event_type in _MOVE_TYPES]
    paired_ids: set[int] = set()
    pairs: list[TransferPair] = []
    for event in moves:
        if event.direction != "-" or event.id in paired_ids:
            continue
        for linked_id in _linked_ids(event):
            counterpart = by_id.get(linked_id)
            if counterpart and counterpart.event_type in _MOVE_TYPES and counterpart.direction == "+" and counterpart.id not in paired_ids:
                pairs.append(TransferPair(withdrawal=event, deposit=counterpart))
                paired_ids.add(event.id)
                paired_ids.add(counterpart.id)
                break
    ambiguous = [e for e in moves if e.id not in paired_ids]
    return pairs, ambiguous


_LINK_WINDOW = timedelta(hours=48)
_LINK_TOLERANCE = Decimal("0.02")


def _suggest_transfer_link(event: EffectiveEvent, candidates: list[EffectiveEvent]) -> tuple[EffectiveEvent, str] | None:
    """Read-only version of the same tx_hash/amount+time heuristic the
    reconciliation matcher already uses (core/reconciliation/matcher.py) —
    reused here so an ambiguous transfer's readiness issue can offer a
    one-click "Link" suggestion instead of making the user search for the
    counterpart by hand. Never links anything itself."""
    is_outgoing = event.direction == "-"
    if event.tx_hash:
        for candidate in candidates:
            if candidate.id != event.id and candidate.tx_hash == event.tx_hash and candidate.direction != event.direction:
                return candidate, "high"

    event_amount = Decimal(event.primary_amount)
    best: tuple[EffectiveEvent, str] | None = None
    for candidate in candidates:
        if candidate.id == event.id or candidate.direction == event.direction:
            continue
        if candidate.primary_asset_id != event.primary_asset_id:
            continue
        if candidate.account_id == event.account_id:
            continue
        delta = abs((candidate.occurred_at - event.occurred_at))
        if delta > _LINK_WINDOW:
            continue
        candidate_amount = Decimal(candidate.primary_amount)
        sent, received = (event_amount, candidate_amount) if is_outgoing else (candidate_amount, event_amount)
        if received > sent or received < sent * (1 - _LINK_TOLERANCE):
            continue
        best = (candidate, "medium")
    return best


def uncovered_type_warning(year_events: list[EffectiveEvent], covered_types: set[str], skip_ids: set[int]) -> str | None:
    """Some event types (staking/lending/LP deposits, margin, futures,
    liquidations, NFT buys, manual adjustments, ...) don't have a safe
    default tax treatment — whether they're a disposal, a non-taxable move,
    or neither depends on protocol-specific facts this ledger doesn't know.
    Rather than guess, they're left out of the cost-basis walk entirely;
    this surfaces that omission instead of hiding it (plan §95)."""
    uncovered = sorted({e.event_type for e in year_events if e.id not in skip_ids and e.event_type not in covered_types})
    if not uncovered:
        return None
    return (
        "These event types aren't included in the automatic gain/loss calculation because their tax "
        "treatment depends on facts this ledger can't infer (e.g. whether a protocol interaction changes "
        f"the underlying asset): {', '.join(uncovered)}. Review them manually if any occurred this tax year."
    )


def build_readiness(
    country: str,
    tax_year: int,
    session: Session,
    events: list[EffectiveEvent],
    currency: str,
    ambiguous_transfers: list[EffectiveEvent],
) -> TaxReadinessResult:
    year_events = [e for e in events if e.occurred_at.year == tax_year]
    year_event_ids = [e.id for e in year_events]

    manual_count = sum(1 for e in year_events if e.provenance == "manual")
    needs_review = [e for e in year_events if e.status == "REQUIRES_REVIEW"]
    unresolved = (
        session.query(Issue).filter(Issue.resolved.is_(False), Issue.event_id.in_(year_event_ids)).count()
        if year_event_ids
        else 0
    )
    priced = sum(
        1
        for e in year_events
        if e.primary_asset.asset_type == "FIAT" or any(v.quote_currency == currency for v in e.valuations)
    )
    missing_price = len(year_events) - priced

    issues: list[ReadinessIssue] = []
    for event in ambiguous_transfers:
        if event.occurred_at.year != tax_year and event.id not in year_event_ids:
            continue
        link = _suggest_transfer_link(event, ambiguous_transfers)
        detail = (
            f"{event.event_type.title()} of {event.primary_amount} {event.primary_asset.symbol} on "
            f"{event.occurred_at.date()} isn't linked as an internal transfer and isn't otherwise "
            "categorized — link it to its counterpart or correct its type before generating a report."
        )
        if link:
            candidate, confidence = link
            detail += (
                f" A likely match was found: {candidate.event_type.title()} of {candidate.primary_amount} "
                f"{candidate.primary_asset.symbol} on {candidate.occurred_at.date()} ({confidence} confidence)."
            )
        issues.append(
            ReadinessIssue(
                severity="blocking",
                event_id=event.id,
                title="Unclassified transfer",
                detail=detail,
                suggested_link_event_id=link[0].id if link else None,
                suggested_link_confidence=link[1] if link else None,
            )
        )
    if missing_price:
        issues.append(
            ReadinessIssue(
                severity="blocking",
                title="Missing prices",
                detail=f"{missing_price} event(s) in {tax_year} have no {currency} valuation yet.",
            )
        )
    if needs_review:
        issues.append(
            ReadinessIssue(
                severity="blocking",
                title="Events awaiting review",
                detail=f"{len(needs_review)} event(s) in {tax_year} are still flagged REQUIRES_REVIEW.",
            )
        )
    if manual_count:
        issues.append(
            ReadinessIssue(
                severity="warning",
                title="Manual entries present",
                detail=f"{manual_count} manually-entered event(s) in {tax_year} aren't independently verified.",
            )
        )
    if unresolved:
        issues.append(
            ReadinessIssue(
                severity="warning",
                title="Unresolved data-quality issues",
                detail=f"{unresolved} unresolved issue(s) touch {tax_year} events.",
            )
        )

    # Raw evidence complete (plan §94/§95): an automatically-imported event
    # should always carry the raw payload it was normalized from. Manual
    # entries legitimately have none — they're covered by the "manual
    # entries present" check above instead.
    missing_evidence = [e for e in year_events if e.provenance == "automatic" and e.raw_event_id is None]
    if missing_evidence:
        issues.append(
            ReadinessIssue(
                severity="warning",
                title="Raw evidence missing",
                detail=(
                    f"{len(missing_evidence)} automatically-imported event(s) in {tax_year} have no raw source "
                    "payload on file."
                ),
            )
        )

    # Fees complete: a fee in the event's own primary asset is priced for
    # free (the event's own valuation covers it); a fee in a different
    # asset needs its own cached price to convert to the report currency.
    unpriced_fee_events = 0
    for event in year_events:
        for fee in event.fees:
            if fee.fee_asset_id == event.primary_asset_id:
                continue
            has_cached_price = (
                session.query(PriceObservation)
                .filter(
                    PriceObservation.provider_asset_id == (fee.fee_asset.coingecko_id or ""),
                    PriceObservation.quote_currency == currency,
                    PriceObservation.observation_date == event.occurred_at.date().isoformat(),
                )
                .first()
                is not None
            )
            if not fee.fee_asset.coingecko_id or not has_cached_price:
                unpriced_fee_events += 1
                break
    if unpriced_fee_events:
        issues.append(
            ReadinessIssue(
                severity="warning",
                title="Fees may be incomplete",
                detail=(
                    f"{unpriced_fee_events} event(s) in {tax_year} have a fee in a different asset than the "
                    f"event itself with no cached {currency} price for that day — report generation will try to "
                    "fetch it live, and fall back to excluding that fee if no price is available."
                ),
            )
        )

    # Sources synchronized: a connected, non-manual, non-paused, non-archived
    # account that's never synced or last failed to sync could be silently
    # missing recent events from this tax year.
    connector_accounts = (
        session.query(Account)
        .filter(Account.connector_type != "manual", Account.archived_at.is_(None), Account.paused.is_(False))
        .all()
    )
    unsynced = [a for a in connector_accounts if a.last_sync is None or a.status == "error"]
    if unsynced:
        issues.append(
            ReadinessIssue(
                severity="warning",
                title="Sources not fully synchronized",
                detail=(
                    f"{len(unsynced)} connected source(s) — {', '.join(a.name for a in unsynced)} — have never "
                    "synced or last failed to sync. Recent activity may be missing from this report."
                ),
            )
        )

    account_names = {name for (name,) in session.query(Account.name).all()}
    for event in year_events:
        suggestion = suggest_reclassification(event, account_names)
        if suggestion is None:
            continue
        issues.append(
            ReadinessIssue(
                severity="warning",
                event_id=event.id,
                title="Manual entry could be reclassified",
                detail=(
                    f"This looks like it could be {suggestion.event_type} because {suggestion.reason}. "
                    "Apply the suggestion or leave it as-is if it doesn't fit."
                ),
                suggested_event_type=suggestion.event_type,
            )
        )

    ready = not any(i.severity == "blocking" for i in issues)
    return TaxReadinessResult(
        country=country,
        tax_year=tax_year,
        event_count=len(year_events),
        priced_event_count=priced,
        missing_price_count=missing_price,
        manual_event_count=manual_count,
        unresolved_issue_count=unresolved,
        ambiguous_transfer_count=len([e for e in ambiguous_transfers if e.id in year_event_ids]),
        unsynced_source_count=len(unsynced),
        unpriced_fee_count=unpriced_fee_events,
        missing_raw_evidence_count=len(missing_evidence),
        issues=issues,
        ready=ready,
    )


_SCHEDULE_ROW_LIMIT = 500


def build_supplementary_rows(
    session: Session,
    events: list[EffectiveEvent],
    pairs: list[TransferPair],
    tax_year: int,
) -> tuple[list[TransferRow], list[CorrectionRow], list[EventScheduleRow], int, ReconciliationSummary]:
    """Shared by every country adapter (plan §66): the "Transfers", "Manual
    corrections", "Detailed event schedule", and "Reconciliation status"
    PDF sections don't depend on jurisdiction-specific tax treatment, so
    there's no reason for es/adapter.py and se/adapter.py to each compute
    them independently."""
    year_events = [e for e in events if e.occurred_at.year == tax_year]
    year_event_ids = {e.id for e in year_events}

    transfer_rows = [
        TransferRow(
            occurred_at=pair.withdrawal.occurred_at,
            asset=pair.withdrawal.primary_asset.symbol,
            quantity=pair.withdrawal.primary_amount,
            from_label=pair.withdrawal.source_label,
            to_label=pair.deposit.source_label,
            event_ids=[pair.withdrawal.id, pair.deposit.id],
        )
        for pair in pairs
        if pair.withdrawal.id in year_event_ids or pair.deposit.id in year_event_ids
    ]

    overrides = (
        session.query(Override)
        .filter(Override.event_id.in_(year_event_ids))
        .order_by(Override.changed_at)
        .all()
        if year_event_ids
        else []
    )
    by_id = {e.id: e for e in year_events}
    correction_rows = [
        CorrectionRow(
            event_id=o.event_id,
            occurred_at=by_id[o.event_id].occurred_at,
            field=o.field,
            old_value=o.old_value or "",
            new_value=o.new_value or "",
            changed_at=o.changed_at,
        )
        for o in overrides
        if o.event_id in by_id
    ]

    truncated = year_events[:_SCHEDULE_ROW_LIMIT]
    event_schedule_rows = [
        EventScheduleRow(
            event_id=e.id,
            occurred_at=e.occurred_at,
            event_type=e.event_type,
            asset=e.primary_asset.symbol,
            amount=e.primary_amount,
            secondary_asset=e.secondary_asset.symbol if e.secondary_asset else None,
            secondary_amount=e.secondary_amount,
            counterparty=e.counterparty,
        )
        for e in truncated
    ]

    unresolved = (
        session.query(Issue).filter(Issue.resolved.is_(False), Issue.event_id.in_(year_event_ids)).count()
        if year_event_ids
        else 0
    )
    reconciliation = ReconciliationSummary(
        linked_transfer_count=len(transfer_rows),
        unresolved_issue_count=unresolved,
        manual_event_count=sum(1 for e in year_events if e.provenance == "manual"),
    )

    return transfer_rows, correction_rows, event_schedule_rows, len(year_events), reconciliation
