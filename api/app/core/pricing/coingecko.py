from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from .provider import DayPrices

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoProvider:
    """First-pass market data provider (plan §41). Never treated as permanent
    storage — every result flows through the price cache before use."""

    name = "CoinGecko"

    def __init__(self, timeout: float = 10.0, api_key: str | None = None):
        self._timeout = timeout
        self._api_key = api_key

    def fetch_day(self, provider_asset_id: str, at: datetime, quote_currencies: list[str]) -> DayPrices | None:
        date_str = at.strftime("%d-%m-%Y")
        headers = {}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        try:
            response = httpx.get(
                f"{COINGECKO_BASE}/coins/{provider_asset_id}/history",
                params={"date": date_str, "localization": "false"},
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        market = (data.get("market_data") or {}).get("current_price") or {}
        prices: dict[str, Decimal] = {}
        for currency in quote_currencies:
            value = market.get(currency.lower())
            if value is not None:
                prices[currency.upper()] = Decimal(str(value))
        if not prices:
            return None
        return DayPrices(provider=self.name, provider_asset_id=provider_asset_id, method="DAILY_REFERENCE", prices=prices)

    def fetch_range(self, provider_asset_id: str, currency: str, days: int) -> list[tuple[datetime, Decimal]] | None:
        """Recent price trend for a small on-page chart (not evidence, not
        cached to disk — plan §41's cache is for point-in-time valuations
        used in tax math; this is display-only and safe to just re-fetch)."""
        headers = {}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        try:
            response = httpx.get(
                f"{COINGECKO_BASE}/coins/{provider_asset_id}/market_chart",
                params={"vs_currency": currency.lower(), "days": days},
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        raw_points = data.get("prices") or []
        points = [
            (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc), Decimal(str(price)))
            for ts_ms, price in raw_points
            if price is not None
        ]
        return points or None
