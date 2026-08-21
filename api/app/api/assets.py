from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload

from app.core.ledger.spam import suspicious_transaction_hashes
from app.db.models import Asset, Event

from .deps import get_session

router = APIRouter(prefix="/api/assets", tags=["assets"])


class AssetVisibilityPatch(BaseModel):
    is_blocked: bool


def _serialize(session: Session, asset: Asset, spam_asset_ids: set[int]) -> dict:
    event_count = session.query(Event).filter(
        (Event.primary_asset_id == asset.id) | (Event.secondary_asset_id == asset.id)
    ).count()
    return {
        "id": asset.id,
        "symbol": asset.symbol,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "network": asset.network,
        "contract_address": asset.contract_address,
        "is_blocked": asset.is_blocked,
        "spam_suspected": asset.id in spam_asset_ids,
        "event_count": event_count,
    }


@router.get("")
def list_assets(
    blocked_only: bool = False,
    search: str | None = None,
    session: Session = Depends(get_session),
):
    query = session.query(Asset)
    if blocked_only:
        query = query.filter(Asset.is_blocked.is_(True))
    if search and search.strip():
        needle = f"%{search.strip()}%"
        query = query.filter(Asset.symbol.ilike(needle) | Asset.name.ilike(needle))
    assets = query.order_by(Asset.is_blocked.desc(), Asset.symbol, Asset.network, Asset.id).all()
    events = session.query(Event).options(selectinload(Event.raw_event)).all()
    suspicious_hashes = suspicious_transaction_hashes(events)
    spam_asset_ids = {
        asset_id
        for event in events
        if str(event.tx_hash or "").lower() in suspicious_hashes
        for asset_id in (event.primary_asset_id, event.secondary_asset_id)
        if asset_id is not None
    }
    return [_serialize(session, asset, spam_asset_ids) for asset in assets]


@router.patch("/{asset_id}")
def update_asset_visibility(
    asset_id: int,
    body: AssetVisibilityPatch,
    session: Session = Depends(get_session),
):
    asset = session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    if asset.asset_type == "FIAT" and body.is_blocked:
        raise HTTPException(400, "Fiat currencies cannot be blocked")
    asset.is_blocked = body.is_blocked
    session.commit()
    return _serialize(session, asset, set())
