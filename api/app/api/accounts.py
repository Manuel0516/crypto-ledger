from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.connectors.evm.connector import CHAINS
from app.core.ledger.sync import SYNCABLE_TYPES, sync_account
from app.db.models import Account, Event
from app.security.secrets import encrypt_config

from .deps import get_session

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# connector_type -> coarse UI grouping kind
_DEFAULT_KIND = {
    "manual": "manual",
    "exchange_import": "exchange",
    "bitcoin_address": "wallet",
    "evm_address": "wallet",
    "solana_address": "wallet",
    "monero_rpc": "wallet",
    "lightning_node": "wallet",
    "bitget_live": "exchange",
    "binance_live": "exchange",
}


def _validate_chain_network(connector_type: str, chain_network: str | None) -> None:
    if connector_type == "evm_address" and chain_network not in (None, *CHAINS):
        raise HTTPException(400, f"Unknown EVM network '{chain_network}'")


def _serialize(session: Session, account: Account) -> dict:
    event_count = session.query(Event).filter(Event.account_id == account.id).count()
    return {
        "id": account.id,
        "name": account.name,
        "kind": account.kind,
        "connector_type": account.connector_type,
        "chain_network": account.chain_network,
        "address": account.address,
        "has_config": bool(account.config_encrypted),
        "syncable": account.connector_type in SYNCABLE_TYPES,
        "status": account.status,
        "wallet_software": account.wallet_software,
        "note": account.note,
        "last_sync": account.last_sync.isoformat() if account.last_sync else None,
        "archived_at": account.archived_at.isoformat() if account.archived_at else None,
        "paused": account.paused,
        "event_count": event_count,
    }


@router.get("")
def list_accounts(include_archived: bool = False, session: Session = Depends(get_session)):
    query = session.query(Account)
    if not include_archived:
        query = query.filter(Account.archived_at.is_(None))
    return [_serialize(session, a) for a in query.order_by(Account.id).all()]


class NewAccount(BaseModel):
    name: str = Field(min_length=1)
    connector_type: str = "manual"
    kind: str | None = None
    wallet_software: str | None = None
    note: str | None = None
    chain_network: str | None = None
    address: str | None = None
    config: dict | None = None


@router.post("")
def create_account(body: NewAccount, session: Session = Depends(get_session)):
    if body.connector_type not in _DEFAULT_KIND:
        raise HTTPException(400, f"Unknown connector_type '{body.connector_type}'")
    _validate_chain_network(body.connector_type, body.chain_network)
    account = Account(
        name=body.name,
        kind=body.kind or _DEFAULT_KIND[body.connector_type],
        connector_type=body.connector_type,
        chain_network=(body.chain_network or "ethereum") if body.connector_type == "evm_address" else body.chain_network,
        address=body.address,
        config_encrypted=encrypt_config(body.config) if body.config else None,
        status="not_configured",
        wallet_software=body.wallet_software,
        note=body.note,
    )
    session.add(account)
    session.commit()
    return _serialize(session, account)


class AccountEdit(BaseModel):
    name: str | None = None
    wallet_software: str | None = None
    note: str | None = None
    chain_network: str | None = None
    address: str | None = None
    config: dict | None = None


@router.patch("/{account_id}")
def edit_account(account_id: int, body: AccountEdit, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    _validate_chain_network(account.connector_type, body.chain_network)
    if body.name is not None:
        account.name = body.name
    if body.wallet_software is not None:
        account.wallet_software = body.wallet_software or None
    if body.note is not None:
        account.note = body.note or None
    if body.chain_network is not None:
        account.chain_network = body.chain_network or None
    if body.address is not None:
        account.address = body.address or None
    if body.config is not None:
        account.config_encrypted = encrypt_config(body.config) if body.config else None
    session.commit()
    return _serialize(session, account)


@router.post("/{account_id}/sync")
def sync(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if account.archived_at is not None:
        raise HTTPException(400, "This source is archived — restore it before syncing")
    if account.paused:
        raise HTTPException(400, "This source is paused — resume it before syncing")
    result = sync_account(session, account)
    return {"status": result.status, "imported": result.imported, "skipped": result.skipped, "message": result.message}


@router.post("/{account_id}/backfill")
def backfill(account_id: int, session: Session = Depends(get_session)):
    """A deeper one-off historical pull — run once after connecting a
    source, distinct from the routine incremental sync (plan §28)."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if account.archived_at is not None:
        raise HTTPException(400, "This source is archived — restore it before syncing")
    if account.paused:
        raise HTTPException(400, "This source is paused — resume it before syncing")
    result = sync_account(session, account, backfill=True)
    return {"status": result.status, "imported": result.imported, "skipped": result.skipped, "message": result.message}


@router.post("/{account_id}/pause")
def pause(account_id: int, session: Session = Depends(get_session)):
    """Temporarily stops automatic syncing without hiding the source (plan
    §89) — unlike Archive, it stays visible in Linked Accounts and can be
    resumed with one click."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    account.paused = True
    session.commit()
    return _serialize(session, account)


@router.post("/{account_id}/resume")
def resume(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    account.paused = False
    session.commit()
    return _serialize(session, account)


@router.post("/{account_id}/archive")
def archive(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    account.archived_at = datetime.now(timezone.utc)
    account.status = "archived"
    session.commit()
    return _serialize(session, account)


@router.post("/{account_id}/unarchive")
def unarchive(account_id: int, session: Session = Depends(get_session)):
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    account.archived_at = None
    account.status = "connected" if account.last_sync else "not_configured"
    session.commit()
    return _serialize(session, account)


@router.delete("/{account_id}")
def delete_account(account_id: int, confirm: bool = False, session: Session = Depends(get_session)):
    """Permanently removes the source registration and its encrypted
    credentials. This is the explicit, harder-to-reach 'advanced operation'
    the plan calls for (§87) — everything else (archive) is reversible.

    Historical events stay in the ledger: their account_id is cleared but
    the event itself, its source_label, and its raw evidence are untouched.
    Financial history is never deleted just because a source connection is
    removed."""
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "Account not found")
    if not confirm:
        raise HTTPException(400, "Pass confirm=true to permanently delete this source")
    session.query(Event).filter(Event.account_id == account.id).update({Event.account_id: None})
    session.delete(account)
    session.commit()
    return {"deleted": account_id}
