from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .common import EffectiveEvent


@dataclass
class Suggestion:
    event_type: str
    reason: str


def suggest_reclassification(event: "EffectiveEvent", account_names: set[str]) -> Suggestion | None:
    """Best-effort suggestion for a MANUAL_ADJUSTMENT event, based only on
    fields the user already filled in — never guessed from nothing. Every
    rule here is deliberately conservative: it's offered as a one-click
    suggestion the user accepts or ignores (via the existing event_type
    override mechanism), never applied automatically. A signal too weak to
    defend in the reason string is left alone rather than forced into a
    category (plan §95's "deficiencies must never be hidden" cuts both ways
    — a wrong guess hidden inside a clean-looking report is worse than an
    honest 'still needs review')."""
    if event.event_type != "MANUAL_ADJUSTMENT":
        return None

    # A second asset leg is close to definitional for a swap — nothing else
    # in the canonical model populates secondary_asset_id/secondary_amount.
    if event.secondary_asset_id and event.secondary_amount:
        return Suggestion("SWAP", "a second asset/amount is recorded on this event, which only a swap has")

    destination = event.destination_label
    counterparty = event.counterparty

    if event.direction == "-":
        if destination and destination in account_names:
            return Suggestion("TRANSFER", f"the destination ('{destination}') matches one of your own linked accounts")
        if counterparty:
            return Suggestion("PAYMENT", f"a counterparty ('{counterparty}') is recorded with an outgoing amount")
        if event.address_to:
            return Suggestion("WITHDRAWAL", "a destination address is recorded with an outgoing amount")
        return None

    if event.direction == "+":
        if counterparty:
            return Suggestion("GIFT_RECEIVED", f"a counterparty ('{counterparty}') is recorded with an incoming amount")
        if event.address_from:
            return Suggestion("DEPOSIT", "a source address is recorded with an incoming amount")
        return None

    return None
