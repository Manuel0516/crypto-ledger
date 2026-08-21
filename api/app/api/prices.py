from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.assets.registry import KNOWN_ASSETS, resolve_network
from app.core.pricing.cache import get_historical_prices
from app.core.pricing.config import configured_price_provider
from app.db.models import Asset

from .deps import get_session

router = APIRouter(prefix="/api/prices", tags=["prices"])

_CACHE_TTL_SECONDS = 15 * 60
_cache: dict[tuple[int, str, int], tuple[float, list[tuple[str, float]]]] = {}


@router.get("/history")
def price_history(asset_id: int, currency: str = "EUR", days: int = 7, session: Session = Depends(get_session)):
    """Recent price trend for the small chart on the Overview page — display
    only, not persisted evidence (contrast with Valuation/PriceObservation,
    which back tax figures and must be reproducible)."""
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    if not asset.coingecko_id:
        return {"symbol": asset.symbol, "currency": currency.upper(), "points": [], "change_pct": None}

    cache_key = (asset_id, currency.upper(), days)
    cached = _cache.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        points = cached[1]
    else:
        provider = configured_price_provider(session)
        raw = provider.fetch_range(asset.coingecko_id, currency, days)
        points = [(t.isoformat(), float(p)) for t, p in raw] if raw else []
        _cache[cache_key] = (now, points)

    change_pct = None
    if len(points) >= 2 and points[0][1]:
        change_pct = round((points[-1][1] - points[0][1]) / points[0][1] * 100, 2)

    return {
        "symbol": asset.symbol,
        "currency": currency.upper(),
        "points": [{"t": t, "price": p} for t, p in points],
        "change_pct": change_pct,
    }


def _market_asset_id(session: Session, symbol: str, network: str | None, provider) -> str | None:
    """Find a persisted asset identity, or resolve a manual ticker without
    creating an asset just because somebody previewed a price in a form."""
    symbol = symbol.strip().upper()
    resolved_network = resolve_network(symbol, network.strip() if network else None)
    query = session.query(Asset).filter(Asset.symbol == symbol, Asset.contract_address.is_(None))
    if resolved_network is None:
        query = query.filter(Asset.network.is_(None))
    else:
        query = query.filter(Asset.network == resolved_network)
    asset = query.first()

    provider_asset_id = asset.coingecko_id if asset is not None else None
    if provider_asset_id is None:
        provider_asset_id = KNOWN_ASSETS.get(symbol, {}).get("coingecko_id")
    if provider_asset_id:
        return str(provider_asset_id)

    resolve_symbol = getattr(provider, "resolve_symbol", None)
    if resolve_symbol is None:
        return None
    try:
        resolved_id = resolve_symbol(symbol)
    except Exception:
        return None
    if resolved_id and asset is not None:
        asset.coingecko_id = resolved_id
    return resolved_id


@router.get("/historical")
def historical_prices(
    symbol: str,
    at: datetime,
    amount: str,
    network: str | None = None,
    currencies: str = Query("EUR,SEK", description="Comma-separated quote currencies"),
    session: Session = Depends(get_session),
):
    """Preview market prices for a form at the activity's timestamp.

    This deliberately uses the same local-cache-first path as automatic
    ledger valuations. It returns both unit prices and totals so manual EUR/
    SEK fields and unit-price editors can use the exact same lookup.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(422, "Asset symbol is required")
    try:
        quantity = abs(Decimal(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(422, "Amount must be a decimal number")
    if quantity <= 0:
        raise HTTPException(422, "Amount must be greater than zero")
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)

    quote_currencies = list(dict.fromkeys(item.strip().upper() for item in currencies.split(",") if item.strip()))
    if not quote_currencies:
        raise HTTPException(422, "At least one quote currency is required")
    provider = configured_price_provider(session)
    provider_asset_id = _market_asset_id(session, symbol, network, provider)
    if not provider_asset_id:
        raise HTTPException(422, f"No market price source is configured for {symbol}")

    try:
        prices = get_historical_prices(session, provider, provider_asset_id, at, quote_currencies)
    except Exception as exc:
        session.rollback()
        raise HTTPException(503, f"Could not retrieve the historical price for {symbol}: {exc}")

    if not prices:
        raise HTTPException(404, f"No historical market price was found for {symbol} at that time")

    session.commit()
    return {
        "symbol": symbol,
        "provider": provider.name,
        "provider_asset_id": provider_asset_id,
        "at": at.isoformat(),
        "prices": {
            currency: {
                "unit_price": str(quote.unit_price),
                "total_value": str((quantity * quote.unit_price).quantize(Decimal("0.01"))),
                "method": quote.method,
                "granularity": quote.granularity,
                "observation_timestamp": quote.observation_timestamp.isoformat(),
            }
            for currency, quote in prices.items()
        },
        "missing": [currency for currency in quote_currencies if currency not in prices],
    }
