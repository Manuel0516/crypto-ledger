from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Event, Issue, RawEvent, Valuation


def get_readiness(session: Session) -> dict:
    event_count = session.query(Event).count()
    unresolved = session.query(Issue).filter_by(resolved=False).count()
    return {
        "events": event_count,
        "raw_evidence": session.query(RawEvent).count(),
        "prices": session.query(Valuation).count(),
        "unresolved_issues": unresolved,
        "ready": unresolved == 0 and event_count > 0,
    }
