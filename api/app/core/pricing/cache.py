from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import PriceObservation

from .provider import PriceProvider


def get_historical_prices(
    session: Session,
    provider: PriceProvider,
    provider_asset_id: str,
    at: datetime,
    quote_currencies: list[str],
) -> dict[str, tuple[Decimal, str]]:
    """Local-cache-first historical price lookup. Only currencies actually
    resolved (from cache or a fresh fetch) are present in the result — a
    provider outage never raises, it just resolves fewer currencies."""
    date_str = at.date().isoformat()
    result: dict[str, tuple[Decimal, str]] = {}
    missing: list[str] = []

    for currency in quote_currencies:
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
            result[currency] = (Decimal(cached.unit_price), cached.method)
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
                        unit_price=str(price),
                        method=fetched.method,
                    )
                )
                result[currency] = (price, fetched.method)
            session.flush()

    return result
