"""Canonical activity taxonomy and safe normalization helpers.

The ledger must never reject or discard unfamiliar source activity. Unknown
source labels are preserved in ``event_subtype`` while the canonical type is
``UNKNOWN`` so review queues and reports remain consistent.
"""

from __future__ import annotations

CANONICAL_EVENT_TYPES = frozenset(
    {
        "BUY", "SELL", "SWAP", "DEPOSIT", "WITHDRAWAL", "TRANSFER", "SEND", "RECEIVE", "PAYMENT",
        "STAKING_DEPOSIT", "STAKING_WITHDRAWAL", "STAKING_REWARD", "INTEREST", "YIELD",
        "LENDING_DEPOSIT", "LENDING_WITHDRAWAL", "LENDING_REWARD", "MINING_REWARD", "AIRDROP",
        "CASHBACK", "REFERRAL_REWARD", "INCOME", "FEE", "NETWORK_FEE", "GAS_FEE", "EXCHANGE_FEE",
        "TRADING_FEE", "FUNDING_FEE", "MARGIN_BORROW", "MARGIN_REPAY", "MARGIN_INTEREST",
        "FUTURES_OPEN", "FUTURES_CLOSE", "FUTURES_PNL", "OPTION_TRADE", "OPTION_EXERCISE", "FUNDING_PAYMENT", "LIQUIDATION",
        "NFT_MINT", "NFT_BUY", "NFT_SELL", "NFT_TRANSFER",
        "BRIDGE_OUT", "BRIDGE_IN", "LIGHTNING_SEND", "LIGHTNING_RECEIVE", "LIGHTNING_CHANNEL_OPEN",
        "LIGHTNING_CHANNEL_CLOSE", "LIGHTNING_FEE", "TOKEN_MINT", "TOKEN_BURN", "GIFT_SENT",
        "GIFT_RECEIVED", "DONATION", "LIQUIDITY", "LOST", "STOLEN", "MANUAL_ADJUSTMENT", "UNKNOWN",
    }
)

# Manual entry is intentionally narrower than the canonical import taxonomy.
# Automatic connectors may still emit the full set above; the Activity form
# only offers these user-facing choices.
MANUAL_EVENT_TYPES = frozenset(
    {
        "DEPOSIT",
        "WITHDRAWAL",
        "TRANSFER",
        "PAYMENT",
        "DONATION",
        "SWAP",
        "MINING_REWARD",
        "STAKING_REWARD",
        "LIQUIDITY",
        "MANUAL_ADJUSTMENT",  # opening balances
    }
)


def canonicalize_event_type(event_type: str | None, event_subtype: str | None = None) -> tuple[str, str | None]:
    """Return a supported event type without losing an unfamiliar source type."""
    source_type = (event_type or "UNKNOWN").strip().upper().replace(" ", "_")
    if source_type in CANONICAL_EVENT_TYPES:
        return source_type, event_subtype
    fallback_subtype = event_subtype or f"SOURCE_TYPE:{source_type}"
    return "UNKNOWN", fallback_subtype
