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


class PriceProvider(Protocol):
    name: str

    def fetch_day(
        self, provider_asset_id: str, at: datetime, quote_currencies: list[str]
    ) -> DayPrices | None: ...
