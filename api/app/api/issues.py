from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.ledger.service import refresh_valuations
from app.core.reconciliation.matcher import find_transfer_candidate
from app.db.models import Event, EventLink, Issue

from .deps import get_session

router = APIRouter(prefix="/api/issues", tags=["issues"])

_TRANSFER_ISSUE_TITLE = "Possible internal transfer"
# A price/asset lookup that failed at ingest time is never retried
# automatically on a later sync (see refresh_valuations) — these are the
# two issue titles a retry can actually resolve, since both come from the
# pricing pipeline rather than a real, permanent data problem.
_PRICING_ISSUE_TITLES = ("Unknown asset — no price source", "Missing price")


@router.get("")
def list_issues(session: Session = Depends(get_session)):
    issues = session.query(Issue).filter_by(resolved=False).order_by(Issue.severity, Issue.id).all()
    return [
        {
            "id": i.id,
            "event_id": i.event_id,
            "severity": i.severity,
            "title": i.title,
            "detail": i.detail,
            # The frontend offers a one-click "Link accounts" action only for
            # this issue type — everything else is just dismissed as reviewed.
            "linkable": i.title == _TRANSFER_ISSUE_TITLE,
            "markable": i.title == _TRANSFER_ISSUE_TITLE,
        }
        for i in issues
    ]


@router.post("/{issue_id}/resolve")
def resolve_issue(issue_id: int, session: Session = Depends(get_session)):
    issue = session.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    issue.resolved = True
    session.commit()
    return {"id": issue.id, "resolved": True}


@router.post("/retry-pricing")
def retry_pricing_issues(session: Session = Depends(get_session)):
    """Re-attempts every open "Unknown asset" / "Missing price" issue in one
    pass. These are the two issue types that can go stale on their own: an
    asset CoinGecko couldn't be resolved against at ingest time (see
    CoinGeckoProvider.resolve_symbol) or a day CoinGecko had no price for
    yet can both start working later without anything about the event
    itself changing — but refresh_valuations only ever runs again for an
    event when something about it is edited, so a fixable issue would
    otherwise sit there forever. Safe to call any time: an event that still
    can't be priced just keeps its existing issue, unchanged."""
    issues = (
        session.query(Issue)
        .filter(Issue.title.in_(_PRICING_ISSUE_TITLES), Issue.resolved.is_(False), Issue.event_id.isnot(None))
        .all()
    )
    event_ids = {i.event_id for i in issues}
    events = session.query(Event).filter(Event.id.in_(event_ids)).all() if event_ids else []
    for event in events:
        refresh_valuations(session, event)
    resolved = (
        session.query(Issue)
        .filter(Issue.title.in_(_PRICING_ISSUE_TITLES), Issue.event_id.in_(event_ids), Issue.resolved.is_(True))
        .count()
        if event_ids
        else 0
    )
    session.commit()
    return {"retried": len(events), "resolved": resolved}


@router.post("/{issue_id}/link")
def link_issue_transfer(issue_id: int, session: Session = Depends(get_session)):
    """Resolve a "Possible internal transfer" issue by actually creating the
    link, not just dismissing the warning — matching what "Confirm to link
    it" in the issue text promises."""
    issue = session.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    if issue.title != _TRANSFER_ISSUE_TITLE or issue.event_id is None:
        raise HTTPException(400, "This issue isn't a linkable transfer match")

    event = session.get(Event, issue.event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    candidate = find_transfer_candidate(session, event)
    if candidate is None:
        raise HTTPException(409, "No matching counterpart event found anymore — link it manually from the event page")

    is_outgoing = event.event_type == "WITHDRAWAL"
    from_event, to_event = (event, candidate) if is_outgoing else (candidate, event)
    existing = (
        session.query(EventLink)
        .filter(
            EventLink.relationship_type == "INTERNAL_TRANSFER",
            EventLink.event_id.in_([event.id, candidate.id]),
            EventLink.linked_event_id.in_([event.id, candidate.id]),
        )
        .one_or_none()
    )
    if existing is None:
        session.add(
            EventLink(
                event_id=from_event.id,
                linked_event_id=to_event.id,
                relationship_type="INTERNAL_TRANSFER",
                provenance="manual",
                confidence="high",
            )
        )
    issue.resolved = True
    session.commit()
    return {"id": issue.id, "resolved": True, "linked_event_id": candidate.id}


@router.post("/{issue_id}/mark-internal")
def mark_issue_internal(issue_id: int, session: Session = Depends(get_session)):
    """Resolve a possible-transfer issue without requiring a counterpart.

    This is deliberately an explicit user action: it means the event is yours
    and non-taxable even though the other side is not represented here.
    """
    issue = session.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    if issue.title != _TRANSFER_ISSUE_TITLE or issue.event_id is None:
        raise HTTPException(400, "This issue isn't a possible internal transfer")
    event = session.get(Event, issue.event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    event.internal_transfer = True
    # If the matcher found the other leg, classify both events explicitly but
    # still do not create an EventLink. This lets the user accept the transfer
    # without making the link itself a prerequisite for report readiness.
    candidate = find_transfer_candidate(session, event)
    if candidate is not None:
        candidate.internal_transfer = True
    issue.resolved = True
    session.commit()
    return {
        "id": issue.id,
        "resolved": True,
        "internal_transfer": True,
        "marked_event_ids": [event.id, candidate.id] if candidate is not None else [event.id],
    }
