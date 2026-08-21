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

    events = session.query(Event).all()
    asset_ids: set[int] = set()
    for event in events:
        asset_ids.add(event.primary_asset_id)
        if event.secondary_asset_id is not None:
            asset_ids.add(event.secondary_asset_id)
        for fee in event.fees:
            asset_ids.add(fee.fee_asset_id)
    assets_by_id: dict[int, Asset] = (
        {a.id: a for a in session.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}
    )

    holdings: dict[int, Decimal] = {}

    def apply(asset_id: int, delta: Decimal) -> None:
        asset = assets_by_id.get(asset_id)
        if asset is None or asset.asset_type == "FIAT":
            return
        holdings[asset_id] = holdings.get(asset_id, Decimal(0)) + delta

    for event in events:
        apply(event.primary_asset_id, Decimal(event.primary_amount) * (1 if event.direction == "+" else -1))
        for fee in event.fees:
            apply(fee.fee_asset_id, -Decimal(fee.fee_amount))
        # A trade's secondary leg (the quote asset given up or received —
        # see NormalizedEvent.secondary_amount) moves holdings too, opposite
        # the primary leg's direction. Without this, a BUY's quote-asset
        # spend was never subtracted — that asset's holdings only ever grew.
        if event.secondary_asset_id is not None and event.secondary_amount not in (None, ""):
            secondary_direction = "-" if event.direction == "+" else "+"
            apply(event.secondary_asset_id, Decimal(event.secondary_amount) * (1 if secondary_direction == "+" else -1))

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
