from __future__ import annotations

from sqlalchemy.orm import Session, selectinload

from app.db.models import Event, Issue, RawEvent, Valuation
from app.core.ledger.visibility import event_has_blocked_asset


def get_readiness(session: Session) -> dict:
    events = (
        session.query(Event)
        .options(selectinload(Event.primary_asset), selectinload(Event.secondary_asset))
        .all()
    )
    visible_event_ids = {event.id for event in events if not event_has_blocked_asset(event)}
    event_count = len(visible_event_ids)
    unresolved = (
        session.query(Issue)
        .filter(Issue.resolved.is_(False))
        .filter(Issue.event_id.is_(None) | Issue.event_id.in_(visible_event_ids))
        .count()
    )
    price_count = session.query(Valuation).filter(Valuation.event_id.in_(visible_event_ids)).count() if visible_event_ids else 0
    priced_activity_count = session.query(Valuation.event_id).filter(Valuation.event_id.in_(visible_event_ids)).distinct().count() if visible_event_ids else 0
    return {
        "events": event_count,
        "raw_evidence": session.query(RawEvent).count(),
        "prices": price_count,
        "unresolved_issues": unresolved,
        # The report path is available even when the ledger needs review.
        # Warnings explain omissions; they never gate generation.
        "activity_count": event_count,
        "priced_activity_count": priced_activity_count,
        "warning_count": unresolved,
        "incomplete_activity_count": 0,
        "ready": True,
    }
