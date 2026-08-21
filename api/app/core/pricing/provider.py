from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class DayPrices:
    provider: str
    provider_asset_id: str
    method: str
    prices: dict[str, Decimal]  # quote_currency -> unit price


@dataclass(frozen=True)
class HistoricalPrice:
    """One cached historical price with the precision the provider actually
    supplied.  Iteration deliberately preserves the old ``(price, method)``
    call-site shape while allowing valuation code to retain the observation
    timestamp as evidence."""

    unit_price: Decimal
    method: str
    observation_timestamp: datetime
    granularity: str

    def __iter__(self):
        yield self.unit_price
        yield self.method


def historical_unit_price(quote: HistoricalPrice | tuple[Decimal, str]) -> Decimal:
    """Return the unit price from the current or legacy cached-price shape."""
    return quote.unit_price if isinstance(quote, HistoricalPrice) else quote[0]


class PriceProvider(Protocol):
    name: str

    def fetch_current(
        self, provider_asset_ids: list[str], quote_currencies: list[str]
    ) -> dict[str, dict[str, Decimal]]: ...

    def fetch_day(
        self, provider_asset_id: str, at: datetime, quote_currencies: list[str]
    ) -> DayPrices | None: ...
