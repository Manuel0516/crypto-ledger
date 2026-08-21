from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import PriceObservation

from .provider import HistoricalPrice, PriceProvider


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _daily_observation_timestamp(at: datetime) -> datetime:
    at = _utc(at)
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def _intraday_cache_key(at: datetime) -> str:
    """One cache bucket per requested hour. CoinGecko's hourly result is
    stable inside that window and this avoids an API request for every trade
    in a busy account."""
    at = _utc(at)
    return at.strftime("%Y-%m-%dT%H")


def get_historical_prices(
    session: Session,
    provider: PriceProvider,
    provider_asset_id: str,
    at: datetime,
    quote_currencies: list[str],
) -> dict[str, HistoricalPrice]:
    """Local-cache-first historical price lookup. Only currencies actually
    resolved (from cache or a fresh fetch) are present in the result — a
    provider outage never raises, it just resolves fewer currencies."""
    at = _utc(at)
    date_str = at.date().isoformat()
    result: dict[str, HistoricalPrice] = {}
    missing: list[str] = []

    # CoinGecko documents hourly data only for a recent window. Ask for it
    # first when a provider supports it, but never make a historical import
    # depend on an account tier or a live market-data request.
    recent_cutoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=90)
    supports_intraday = getattr(provider, "fetch_near_timestamp", None)
    intraday_missing: list[str] = []
    if supports_intraday is not None and at >= recent_cutoff:
        intraday_key = _intraday_cache_key(at)
        for currency in quote_currencies:
            cached = (
                session.query(PriceObservation)
                .filter_by(
                    provider=provider.name,
                    provider_asset_id=provider_asset_id,
                    quote_currency=currency,
                    observation_date=intraday_key,
                )
                .one_or_none()
            )
            if cached:
                result[currency] = HistoricalPrice(
                    Decimal(cached.unit_price),
                    cached.method,
                    cached.observation_timestamp or _daily_observation_timestamp(at),
                    cached.granularity,
                )
            else:
                intraday_missing.append(currency)
        if intraday_missing:
            try:
                intraday = supports_intraday(provider_asset_id, at, intraday_missing)
            except Exception:
                intraday = {}
            for currency, quote in intraday.items():
                session.add(
                    PriceObservation(
                        provider=provider.name,
                        provider_asset_id=provider_asset_id,
                        quote_currency=currency,
                        observation_date=intraday_key,
                        observation_timestamp=quote.observation_timestamp,
                        unit_price=str(quote.unit_price),
                        method=quote.method,
                        granularity=quote.granularity,
                    )
                )
                result[currency] = quote
            session.flush()

    for currency in quote_currencies:
        if currency in result:
            continue
        cached = (
            session.query(PriceObservation)
            .filter_by(
                provider=provider.name,
                provider_asset_id=provider_asset_id,
                quote_currency=currency,
                observation_date=date_str,
            )
            .one_or_none()
        )
        if cached:
            result[currency] = HistoricalPrice(
                Decimal(cached.unit_price),
                cached.method,
                cached.observation_timestamp or _daily_observation_timestamp(at),
                cached.granularity,
            )
        else:
            missing.append(currency)

    if missing:
        fetched = provider.fetch_day(provider_asset_id, at, missing)
        if fetched:
            for currency, price in fetched.prices.items():
                session.add(
                    PriceObservation(
                        provider=fetched.provider,
                        provider_asset_id=provider_asset_id,
                        quote_currency=currency,
                        observation_date=date_str,
                        observation_timestamp=_daily_observation_timestamp(at),
                        unit_price=str(price),
                        method=fetched.method,
                        granularity="day",
                    )
                )
                result[currency] = HistoricalPrice(
                    price, fetched.method, _daily_observation_timestamp(at), "day"
                )
            session.flush()

    return result
