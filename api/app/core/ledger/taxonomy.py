"""Canonical activity taxonomy and safe normalization helpers.

The ledger must never reject or discard unfamiliar source activity. Unknown
source labels are preserved in ``event_subtype`` while the canonical type is
``UNKNOWN`` so review queues and reports remain consistent.
"""

from __future__ import annotations

# Deliberately small: every type here is either in real use today or is a
# distinct category the tax adapters (and RP2 itself) already treat
# differently from everything else. A type that isn't — margin, futures,
# options, NFT sub-types, bridges, Lightning specifics — folds into the
# closest of these instead of getting its own label; none of those had
# automatic tax treatment before this either (see uncovered_type_warning),
# so folding them changes no report total, only how many labels exist.
CANONICAL_EVENT_TYPES = frozenset(
    {
        "BUY", "SELL", "SWAP", "DEPOSIT", "WITHDRAWAL", "TRANSFER", "PAYMENT",
        "STAKING_DEPOSIT", "STAKING_WITHDRAWAL", "STAKING_REWARD", "INTEREST",
        "MINING_REWARD", "AIRDROP", "INCOME",
        "GIFT_SENT", "GIFT_RECEIVED", "DONATION", "LOST",
        "LIQUIDITY", "MANUAL_ADJUSTMENT", "UNKNOWN",
    }
)

# The Activity form offers every canonical type except UNKNOWN, which only
# ever comes from automatic classification failing to recognize a source
# type — never something a person would deliberately choose.
MANUAL_EVENT_TYPES = CANONICAL_EVENT_TYPES - {"UNKNOWN"}


def canonicalize_event_type(event_type: str | None, event_subtype: str | None = None) -> tuple[str, str | None]:
    """Return a supported event type without losing an unfamiliar source type."""
    source_type = (event_type or "UNKNOWN").strip().upper().replace(" ", "_")
    if source_type in CANONICAL_EVENT_TYPES:
        return source_type, event_subtype
    fallback_subtype = event_subtype or f"SOURCE_TYPE:{source_type}"
    return "UNKNOWN", fallback_subtype
