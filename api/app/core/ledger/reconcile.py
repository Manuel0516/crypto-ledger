from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.connectors.base import Balance, ConnectorUnavailable
from app.core.assets.registry import get_or_create_asset, resolve_network
from app.core.ledger.connectors import build_connector
from app.db.models import Account, AccountBalance, Asset, Event

# Two live balances rarely land on the exact same decimal as the
# ledger-computed total — dust from fee rounding, an exchange truncating a
# display balance, a still-open order holding a sliver back. A tiny gap
# shouldn't read as "your ledger is wrong"; only a gap too big to be
# rounding noise should.
_ABSOLUTE_TOLERANCE = Decimal("0.00000001")
_RELATIVE_TOLERANCE = Decimal("0.001")  # 0.1%


@dataclass(frozen=True)
class ReconciledAsset:
    asset_symbol: str
    asset_network: str | None
    live_amount: str
    computed_amount: str
    difference: str
    matches: bool


@dataclass(frozen=True)
class ReconcileResult:
    status: str  # ok | unsupported | unavailable
    assets: list[ReconciledAsset]
    message: str | None = None


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _matches(live: Decimal, computed: Decimal) -> bool:
    diff = abs(live - computed)
    if diff <= _ABSOLUTE_TOLERANCE:
        return True
    tolerance = max(abs(live), abs(computed)) * _RELATIVE_TOLERANCE
    return diff <= tolerance


def compute_account_holdings(session: Session, account_id: int) -> dict[tuple[str, str | None, str | None], Decimal]:
    """Ledger-computed holdings for one account, keyed the same way a live
    Balance is (symbol, network, contract) — scoped to a single account
    (fiat is excluded, fees are subtracted from the fee asset's own total).

    A trade's secondary leg (the quote asset given up or received — see
    NormalizedEvent.secondary_amount in connectors/base.py) is applied too,
    with the direction opposite the primary leg's, per that field's own
    documented convention. Skipping it would mean a BUY's quote-asset spend
    is never subtracted from holdings — the exact false-mismatch source this
    was written to close."""
    holdings: dict[tuple[str, str | None, str | None], Decimal] = {}
    events = session.query(Event).filter(Event.account_id == account_id).all()

    asset_ids: set[int] = set()
    for event in events:
        asset_ids.add(event.primary_asset_id)
        if event.secondary_asset_id is not None:
            asset_ids.add(event.secondary_asset_id)
        for fee in event.fees:
            asset_ids.add(fee.fee_asset_id)
    assets_by_id = {a.id: a for a in session.query(Asset).filter(Asset.id.in_(asset_ids)).all()} if asset_ids else {}

    def apply(asset_id: int, delta: Decimal) -> None:
        asset = assets_by_id.get(asset_id)
        if asset is None or asset.asset_type == "FIAT":
            return
        key = (asset.symbol.upper(), asset.network, asset.contract_address)
        holdings[key] = holdings.get(key, Decimal(0)) + delta

    for event in events:
        apply(event.primary_asset_id, _decimal(event.primary_amount) * (1 if event.direction == "+" else -1))
        for fee in event.fees:
            apply(fee.fee_asset_id, -_decimal(fee.fee_amount))
        if event.secondary_asset_id is not None and event.secondary_amount not in (None, ""):
            secondary_direction = "-" if event.direction == "+" else "+"
            apply(event.secondary_asset_id, _decimal(event.secondary_amount) * (1 if secondary_direction == "+" else -1))
    return holdings


def store_account_balances(session: Session, account: Account, live_balances: list[Balance]) -> None:
    """Overwrites this account's live-balance snapshot (Overview's source of
    truth for accounts that have one — see AccountBalance) with what was
    just fetched. An asset present in the old snapshot but absent from
    `live_balances` is deleted, not left behind — otherwise a coin that was
    fully withdrawn/converted away would linger as a "phantom" holding
    forever, which is the exact bug this snapshot exists to fix."""
    seen_asset_ids: set[int] = set()
    for balance in live_balances:
        symbol = balance.asset_symbol.upper()
        network = resolve_network(symbol, balance.asset_network, balance.asset_contract)
        asset = get_or_create_asset(
            session,
            symbol,
            network,
            contract_address=balance.asset_contract,
            asset_type=balance.asset_type,
        )
        seen_asset_ids.add(asset.id)
        amount = format(_decimal(balance.amount), "f")
        existing = session.query(AccountBalance).filter_by(account_id=account.id, asset_id=asset.id).one_or_none()
        if existing is not None:
            existing.amount = amount
        else:
            session.add(AccountBalance(account_id=account.id, asset_id=asset.id, amount=amount))

    stale_query = session.query(AccountBalance).filter(AccountBalance.account_id == account.id)
    if seen_asset_ids:
        stale_query = stale_query.filter(~AccountBalance.asset_id.in_(seen_asset_ids))
    for row in stale_query.all():
        session.delete(row)

    account.balance_synced_at = datetime.now(timezone.utc)


def reconcile_account(session: Session, account: Account, connector=None) -> ReconcileResult:
    """Compare live balances (if the connector offers them) against what the
    ledger computes for this account, and refresh this account's
    AccountBalance snapshot (Overview's display source — see
    store_account_balances) with what was just fetched. Never touches the
    event ledger or ingests anything — a mismatch is surfaced for the user
    to investigate, not auto-corrected (plan §48: raw evidence and the
    events derived from it are never silently rewritten).

    Pass an already-built `connector` when the caller (sync_account) has one
    on hand — skips rebuilding it (re-decrypting config, re-detecting a
    Bitget account's classic/UTA mode) a second time right after a sync."""
    if connector is None:
        connector = build_connector(account, session)
    if connector is None:
        return ReconcileResult(status="unsupported", assets=[], message="This source doesn't support automatic sync.")

    fetch_balances = getattr(connector, "fetch_balances", None)
    if fetch_balances is None:
        return ReconcileResult(status="unsupported", assets=[], message="This source doesn't report a live balance to check against.")

    try:
        live_balances = list(fetch_balances())
    except ConnectorUnavailable as exc:
        return ReconcileResult(status="unavailable", assets=[], message=str(exc))

    store_account_balances(session, account, live_balances)

    computed = compute_account_holdings(session, account.id)
    live_by_key: dict[tuple[str, str | None, str | None], Decimal] = {}
    for balance in live_balances:
        symbol = balance.asset_symbol.upper()
        # An exchange balance never carries a network (Bitget/Binance don't
        # have one to give), but the ledger's Asset row for e.g. BTC always
        # does (get_or_create_asset defaults it to "Bitcoin") — resolve the
        # same way here so the keys actually line up instead of producing a
        # phantom "asset only on one side" mismatch for every native coin.
        network = resolve_network(symbol, balance.asset_network, balance.asset_contract)
        key = (symbol, network, balance.asset_contract)
        live_by_key[key] = live_by_key.get(key, Decimal(0)) + _decimal(balance.amount)

    assets: list[ReconciledAsset] = []
    for key in set(live_by_key) | set(computed):
        live_amount = live_by_key.get(key, Decimal(0))
        computed_amount = computed.get(key, Decimal(0))
        if live_amount == 0 and computed_amount == 0:
            continue
        symbol, network, _contract = key
        assets.append(
            ReconciledAsset(
                asset_symbol=symbol,
                asset_network=network,
                live_amount=format(live_amount, "f"),
                computed_amount=format(computed_amount, "f"),
                difference=format(live_amount - computed_amount, "f"),
                matches=_matches(live_amount, computed_amount),
            )
        )
    assets.sort(key=lambda a: (a.matches, a.asset_symbol))
    return ReconcileResult(status="ok", assets=assets)
