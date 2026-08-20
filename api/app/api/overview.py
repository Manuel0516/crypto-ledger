from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.settings import get_or_create_settings
from app.db.models import Asset, Event, Issue, SyncState, Valuation

from .deps import get_session

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
def overview(session: Session = Depends(get_session)):
    settings = get_or_create_settings(session)
    display_currency = settings.display_currency
    holdings: dict[int, Decimal] = {}
    assets_by_id: dict[int, Asset] = {}
    for event, asset in session.query(Event, Asset).join(Asset, Event.primary_asset_id == Asset.id):
        if asset.asset_type == "FIAT":
            continue
        amount = Decimal(event.primary_amount) * (1 if event.direction == "+" else -1)
        holdings[asset.id] = holdings.get(asset.id, Decimal(0)) + amount
        assets_by_id[asset.id] = asset
        for fee in event.fees:
            if fee.fee_asset_id == asset.id:
                holdings[asset.id] -= Decimal(fee.fee_amount)

    # A portfolio value is the current net position valued once per asset. It
    # must not be the sum of every historical transaction valuation.
    latest_prices: dict[tuple[int, str], Decimal] = {}
    for valuation, event in (
        session.query(Valuation, Event)
        .join(Event, Valuation.event_id == Event.id)
        .order_by(Event.occurred_at.desc(), Event.id.desc(), Valuation.id.desc())
        .all()
    ):
        key = (event.primary_asset_id, valuation.quote_currency)
        latest_prices.setdefault(key, Decimal(valuation.unit_price))

    totals = {"EUR": Decimal(0), "SEK": Decimal(0), display_currency: Decimal(0)}
    assets = []
    for asset_id, amount in holdings.items():
        asset = assets_by_id[asset_id]
        if amount == 0:
            continue
        eur_value = amount * latest_prices.get((asset_id, "EUR"), Decimal(0))
        sek_value = amount * latest_prices.get((asset_id, "SEK"), Decimal(0))
        display_value = amount * latest_prices.get((asset_id, display_currency), Decimal(0))
        totals["EUR"] += eur_value
        totals["SEK"] += sek_value
        totals[display_currency] += display_value
        assets.append(
            {
                "id": asset.id,
                "symbol": asset.symbol,
                "name": asset.name,
                "amount": float(amount),
                "value_eur": round(float(eur_value), 2),
                "value_sek": round(float(sek_value), 2),
                "value_display": round(float(display_value), 2),
                "display_currency": display_currency,
            }
        )

    assets.sort(key=lambda item: item["value_display"], reverse=True)

    bitget_sync = session.get(SyncState, "bitget")
    return {
        "portfolio_eur": round(float(totals["EUR"]), 2),
        "portfolio_sek": round(float(totals["SEK"]), 2),
        "display_currency": display_currency,
        "portfolio_display": round(float(totals[display_currency]), 2),
        "assets": assets,
        "issues": session.query(Issue).filter_by(resolved=False).count(),
        "last_sync": bitget_sync.last_sync.isoformat() if bitget_sync and bitget_sync.last_sync else None,
    }
