from __future__ import annotations


def is_blocked_asset(asset) -> bool:
    """Whether an asset is explicitly hidden by the user."""
    return bool(asset is not None and getattr(asset, "is_blocked", False))


def event_has_blocked_asset(event) -> bool:
    """Whether either economic leg of an event uses a blocked asset.

    Fees are intentionally not considered here: a blocked fee token should be
    hidden from balances, but it should not make an otherwise legitimate
    transaction disappear from the ledger or tax calculation.
    """
    return is_blocked_asset(getattr(event, "primary_asset", None)) or is_blocked_asset(
        getattr(event, "secondary_asset", None)
    )
