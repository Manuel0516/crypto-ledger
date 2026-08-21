from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from .provider import DayPrices, HistoricalPrice

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

    def fetch_near_timestamp(
        self, provider_asset_id: str, at: datetime, quote_currencies: list[str]
    ) -> dict[str, HistoricalPrice]:
        """Return CoinGecko's nearest observation around ``at`` when its
        chart API can provide intraday data.

        The endpoint accepts one quote currency at a time.  This method is
        intentionally best-effort: callers use it only for recent activity
        and retain the documented daily-reference fallback when an account's
        plan, retention window, or rate limit cannot supply intraday points.
        """
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.astimezone(timezone.utc)
        lower_bound = int(at.timestamp()) - 60 * 60
        upper_bound = int(at.timestamp()) + 60 * 60
        headers = {}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key

        result: dict[str, HistoricalPrice] = {}
        for currency in quote_currencies:
            try:
                response = httpx.get(
                    f"{COINGECKO_BASE}/coins/{provider_asset_id}/market_chart/range",
                    params={"vs_currency": currency.lower(), "from": lower_bound, "to": upper_bound},
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                points = response.json().get("prices") or []
            except (httpx.HTTPError, ValueError, TypeError):
                continue

            candidates: list[tuple[datetime, Decimal]] = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) < 2 or point[1] is None:
                    continue
                try:
                    observed_at = datetime.fromtimestamp(float(point[0]) / 1000, tz=timezone.utc)
                    candidates.append((observed_at, Decimal(str(point[1]))))
                except (TypeError, ValueError):
                    continue
            if not candidates:
                continue
            observed_at, price = min(candidates, key=lambda point: abs((point[0] - at).total_seconds()))
            distance_seconds = abs((observed_at - at).total_seconds())
            result[currency.upper()] = HistoricalPrice(
                unit_price=price,
                method="NEAREST_5_MIN" if distance_seconds <= 5 * 60 else "NEAREST_HOUR",
                observation_timestamp=observed_at,
                granularity="5m" if distance_seconds <= 5 * 60 else "hour",
            )
        return result

    def resolve_symbol(self, symbol: str) -> str | None:
        """Best-effort ticker -> CoinGecko coin id lookup for an asset this
        app has no hardcoded mapping for (see registry.KNOWN_ASSETS, a small
        MVP list that only covers a handful of majors) — most real coins an
        exchange account actually holds aren't in it. Not part of the
        PriceProvider Protocol (checked via getattr, same convention as
        Connector.fetch_balances) since it's CoinGecko-specific.

        /search results are pre-sorted by market cap descending, so the
        first coin whose ticker matches exactly is the right, unambiguous
        choice for the vast majority of real assets — a low-cap token
        squatting on a major coin's ticker isn't a case this resolves.
        Never raises: a failed lookup just leaves the asset unresolved,
        same as before this existed."""
        headers = {}
        if self._api_key:
            headers["x-cg-demo-api-key"] = self._api_key
        try:
            response = httpx.get(
                f"{COINGECKO_BASE}/search",
                params={"query": symbol},
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        for coin in data.get("coins") or []:
            if str(coin.get("symbol", "")).upper() == symbol.upper():
                coin_id = coin.get("id")
                if coin_id:
                    return str(coin_id)
        return None

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
