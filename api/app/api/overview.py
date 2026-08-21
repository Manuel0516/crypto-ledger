from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.pricing.config import configured_price_provider
from app.core.settings import get_or_create_settings
from app.db.models import Account, AccountBalance, Asset, Event, Issue, SyncState, Valuation

from .deps import get_session

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
def overview(session: Session = Depends(get_session)):
    settings = get_or_create_settings(session)
    display_currency = settings.display_currency

    # Accounts with a live-balance snapshot (see AccountBalance /
    # store_account_balances) report what the source actually says right
    # now — that's the real number, not a derivative of it, so it's used
    # as-is instead of being cross-checked against the computed ledger here
    # (that comparison is what /reconcile is for). Every other account
    # (manual entries, wallet types with no fetch_balances) still needs its
    # holdings computed from events, same as before this feature existed.
    accounts = session.query(Account).order_by(Account.id).all()
    live_account_ids = {a.id for a in accounts if a.balance_synced_at is not None}

    balance_rows = (
        session.query(AccountBalance).filter(AccountBalance.account_id.in_(live_account_ids)).all()
        if live_account_ids
        else []
    )
    events = session.query(Event).all()
    assets_by_id: dict[int, Asset] = {asset.id: asset for asset in session.query(Asset).all()}

    def is_nft(asset_id: int | None) -> bool:
        return bool(asset_id is not None and assets_by_id.get(asset_id) and assets_by_id[asset_id].asset_type == "NFT")

    def event_has_nft(event: Event) -> bool:
        return is_nft(event.primary_asset_id) or is_nft(event.secondary_asset_id)

    # Live snapshots are authoritative for coins and tokens. NFT ownership is
    # deliberately kept from events because the explorer balance endpoints do
    # not expose a portable ERC-721/ERC-1155 inventory. A NULL account_id
    # (manual/legacy events) is always computed too.
    computed_events = [
        event
        for event in events
        if event.account_id not in live_account_ids or event_has_nft(event)
    ]

    holdings: dict[int, Decimal] = {}

    def apply(target: dict[int, Decimal], asset_id: int, delta: Decimal) -> None:
        asset = assets_by_id.get(asset_id)
        if asset is None or asset.asset_type == "FIAT":
            return
        target[asset_id] = target.get(asset_id, Decimal(0)) + delta

    def apply_event(target: dict[int, Decimal], event: Event, *, nft_only: bool = False) -> None:
        primary_delta = Decimal(event.primary_amount) * (1 if event.direction == "+" else -1)
        if not nft_only or is_nft(event.primary_asset_id):
            apply(target, event.primary_asset_id, primary_delta)

        # A live account's NFT transfer should not re-add an already-snapshot-
        # backed gas fee or fungible trade leg. There is no portable NFT
        # snapshot to replace, so only NFT legs are retained in that case.
        if nft_only:
            if event.secondary_asset_id is not None and is_nft(event.secondary_asset_id) and event.secondary_amount not in (None, ""):
                secondary_direction = "-" if event.direction == "+" else "+"
                apply(target, event.secondary_asset_id, Decimal(event.secondary_amount) * (1 if secondary_direction == "+" else -1))
            return

        for fee in event.fees:
            apply(target, fee.fee_asset_id, -Decimal(fee.fee_amount))
        # A trade's secondary leg (the quote asset given up or received —
        # see NormalizedEvent.secondary_amount) moves holdings too, opposite
        # the primary leg's direction.
        if event.secondary_asset_id is not None and event.secondary_amount not in (None, ""):
            secondary_direction = "-" if event.direction == "+" else "+"
            apply(target, event.secondary_asset_id, Decimal(event.secondary_amount) * (1 if secondary_direction == "+" else -1))

    for row in balance_rows:
        apply(holdings, row.asset_id, Decimal(row.amount))

    for event in computed_events:
        apply_event(holdings, event, nft_only=event.account_id in live_account_ids)

    # Keep the account boundary visible all the way to the UI. This is what
    # makes two MetaMask addresses holding the same token understandable,
    # instead of presenting one unexplained combined number.
    account_holdings: dict[int, dict[int, Decimal]] = {account.id: {} for account in accounts}
    for row in balance_rows:
        apply(account_holdings.setdefault(row.account_id, {}), row.asset_id, Decimal(row.amount))
    for event in events:
        if event.account_id is None or event.account_id not in account_holdings:
            continue
        if event.account_id in live_account_ids:
            if event_has_nft(event):
                apply_event(account_holdings[event.account_id], event, nft_only=True)
        else:
            apply_event(account_holdings[event.account_id], event)

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

    # Historical Valuation rows are event evidence and must not be reused as
    # the current portfolio price. Fetch a separate live snapshot for the
    # assets currently held; if the provider is unavailable, retain the most
    # recent historical value as an explicitly best-effort fallback rather
    # than making the whole Overview endpoint fail.
    current_prices: dict[tuple[int, str], Decimal] = {}
    provider_asset_ids = {
        asset.coingecko_id
        for asset_id, amount in holdings.items()
        if amount != 0
        for asset in [assets_by_id.get(asset_id)]
        if asset is not None and asset.coingecko_id
    }
    if provider_asset_ids:
        try:
            current_by_provider = configured_price_provider(session).fetch_current(
                list(provider_asset_ids),
                list(dict.fromkeys(["EUR", "SEK", display_currency])),
            )
        except Exception:
            current_by_provider = {}
        for asset_id, amount in holdings.items():
            if amount == 0:
                continue
            asset = assets_by_id.get(asset_id)
            if asset is None or not asset.coingecko_id:
                continue
            for currency, price in current_by_provider.get(asset.coingecko_id, {}).items():
                current_prices[(asset_id, currency.upper())] = Decimal(str(price))

    totals = {currency: Decimal(0) for currency in ("EUR", "SEK", display_currency)}
    def holding_payload(asset_id: int, amount: Decimal) -> dict:
        asset = assets_by_id[asset_id]
        def price(currency: str) -> Decimal:
            return current_prices.get((asset_id, currency), latest_prices.get((asset_id, currency), Decimal(0)))

        eur_value = amount * price("EUR")
        sek_value = amount * price("SEK")
        display_value = amount * price(display_currency)
        return {
            "id": asset.id,
            "symbol": asset.symbol,
            "name": asset.name,
            "asset_type": asset.asset_type,
            "network": asset.network,
            "contract_address": asset.contract_address,
            "amount": float(amount),
            "value_eur": round(float(eur_value), 2),
            "value_sek": round(float(sek_value), 2),
            "value_display": round(float(display_value), 2),
            "display_currency": display_currency,
        }

    assets = []
    for asset_id, amount in holdings.items():
        # Overview is a holdings view, not a liabilities view. Keep negative
        # ledger balances available in Activity, reconciliation and reports,
        # but do not present them as portfolio assets or subtract them from
        # the headline total.
        if amount <= 0:
            continue
        item = holding_payload(asset_id, amount)
        totals["EUR"] += Decimal(str(item["value_eur"]))
        totals["SEK"] += Decimal(str(item["value_sek"]))
        if display_currency not in {"EUR", "SEK"}:
            totals[display_currency] += Decimal(str(item["value_display"]))
        assets.append(item)

    assets.sort(key=lambda item: item["value_display"], reverse=True)

    account_payloads = []
    for account in accounts:
        balances = [
            holding_payload(asset_id, amount)
            for asset_id, amount in account_holdings.get(account.id, {}).items()
            if amount > 0
        ]
        balances.sort(key=lambda item: (item["value_display"], item["symbol"]), reverse=True)
        account_payloads.append(
            {
                "id": account.id,
                "name": account.name,
                "connector_type": account.connector_type,
                "address": account.address,
                "chain_network": account.chain_network,
                "status": account.status,
                "balance_synced_at": account.balance_synced_at.isoformat() if account.balance_synced_at else None,
                "balances": balances,
            }
        )

    bitget_sync = session.get(SyncState, "bitget")
    sync_times = [account.last_sync for account in accounts if account.last_sync is not None]
    if bitget_sync and bitget_sync.last_sync is not None:
        sync_times.append(bitget_sync.last_sync)
    last_sync = max(sync_times) if sync_times else None
    return {
        "portfolio_eur": round(float(totals["EUR"]), 2),
        "portfolio_sek": round(float(totals["SEK"]), 2),
        "display_currency": display_currency,
        "portfolio_display": round(float(totals[display_currency]), 2),
        "assets": assets,
        "accounts": account_payloads,
        "issues": session.query(Issue).filter_by(resolved=False).count(),
        "last_sync": last_sync.isoformat() if last_sync else None,
    }
