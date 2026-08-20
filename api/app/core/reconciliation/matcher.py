from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Event, Issue

_WINDOW = timedelta(hours=48)
_TOLERANCE = Decimal("0.02")  # the received leg may be up to 2% lower (fees), never higher

_PAIRS = {"WITHDRAWAL": "DEPOSIT", "DEPOSIT": "WITHDRAWAL"}


def find_transfer_candidate(session: Session, event: Event) -> Event | None:
    """The same-asset, opposite-direction, different-account event most
    likely to be the other leg of `event` — tx_hash match preferred over
    amount+time. Read-only: never creates or links anything itself. Shared
    by `match_transfers` (surfacing the issue) and the issue-resolution
    endpoint (creating the link once a person confirms it)."""
    counterpart_type = _PAIRS.get(event.event_type)
    # Without account identity there is no safe way to call two legs an
    # internal transfer. The matcher intentionally refuses to guess.
    if counterpart_type is None or event.account_id is None:
        return None

    is_outgoing = event.event_type == "WITHDRAWAL"
    window_start = event.occurred_at if is_outgoing else event.occurred_at - _WINDOW
    window_end = event.occurred_at + _WINDOW if is_outgoing else event.occurred_at

    candidates = (
        session.query(Event)
        .filter(
            Event.id != event.id,
            Event.primary_asset_id == event.primary_asset_id,
            Event.event_type == counterpart_type,
            Event.account_id != event.account_id,
            Event.occurred_at >= window_start,
            Event.occurred_at <= window_end,
        )
        .all()
    )
    if not candidates:
        return None

    # Exact tx_hash match is much stronger evidence than amount+time
    # heuristics — e.g. a wallet's on-chain deposit and an exchange's
    # withdrawal record for the same withdrawal can both carry the same
    # transaction hash. Prefer it when available (plan §17's structured
    # evidence existing precisely to make this possible).
    if event.tx_hash:
        for candidate in candidates:
            if candidate.tx_hash and candidate.tx_hash == event.tx_hash:
                return candidate

    event_amount = Decimal(event.primary_amount)
    for candidate in candidates:
        candidate_amount = Decimal(candidate.primary_amount)
        sent, received = (event_amount, candidate_amount) if is_outgoing else (candidate_amount, event_amount)
        if received > sent or received < sent * (1 - _TOLERANCE):
            continue
        return candidate
    return None


def match_transfers(session: Session, event: Event) -> Issue | None:
    """Look for the opposite leg of a likely internal transfer (plan §50-51).
    Never auto-links two events — always surfaces a reviewable issue."""
    candidate = find_transfer_candidate(session, event)
    if candidate is None:
        return None

    already_flagged = (
        session.query(Issue)
        .filter(Issue.event_id.in_([event.id, candidate.id]), Issue.title == "Possible internal transfer")
        .first()
        is not None
    )
    if already_flagged:
        return None

    if event.tx_hash and candidate.tx_hash == event.tx_hash:
        detail = (
            f"Both legs share transaction hash {event.tx_hash[:16]}… — this is very likely the same "
            "on-chain transfer seen from two of your accounts. Confirm to link it."
        )
    else:
        is_outgoing = event.event_type == "WITHDRAWAL"
        event_amount = Decimal(event.primary_amount)
        candidate_amount = Decimal(candidate.primary_amount)
        sent, received = (event_amount, candidate_amount) if is_outgoing else (candidate_amount, event_amount)
        fee_estimate = sent - received
        detail = (
            f"Sent {sent} and received {received} of the same asset within {_WINDOW}. "
            f"Difference of {fee_estimate} looks like a network fee. Confirm to link as an internal transfer."
        )

    issue = Issue(event_id=event.id, severity="warning", title="Possible internal transfer", detail=detail)
    session.add(issue)
    session.flush()
    return issue
