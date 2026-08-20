from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.reconciliation.matcher import find_transfer_candidate
from app.db.models import Event, EventLink, Issue

from .deps import get_session

router = APIRouter(prefix="/api/issues", tags=["issues"])

_TRANSFER_ISSUE_TITLE = "Possible internal transfer"


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
