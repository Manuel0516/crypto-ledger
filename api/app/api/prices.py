from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

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
