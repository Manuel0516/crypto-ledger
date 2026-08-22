from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.assets.registry import get_or_create_asset
from app.core.ledger.service import store_raw
from app.core.reconciliation.matcher import match_transfers
from app.connectors.base import RawRecord
from app.db.models import Account, AppSettings, Asset, Event, EventLink, Fee, Override, SyncState, Valuation
from app.security.secrets import encrypt_config

_SEK_PER_EUR = Decimal("11.25")

# Rough, illustrative EUR price trajectories — not real historical data, just
# enough of a trend that buys/sells/staking income across 2024-2026 produce
# genuinely varied (not flat) realized gains and losses in the tax report.
# Linearly interpolated between anchor points; clamped outside the range.
_PRICE_CURVES: dict[str, list[tuple[str, str]]] = {
    "BTC": [
        ("2024-01-01", "39000"), ("2024-06-01", "52000"), ("2024-12-31", "62000"),
        ("2025-06-01", "54000"), ("2025-12-31", "58000"), ("2026-08-20", "59350"),
    ],
    "ETH": [
        ("2024-01-01", "2000"), ("2024-06-01", "2900"), ("2024-12-31", "3200"),
        ("2025-06-01", "2400"), ("2025-12-31", "2700"), ("2026-08-20", "2950"),
    ],
    "SOL": [
        ("2024-01-01", "75"), ("2024-06-01", "130"), ("2024-12-31", "180"),
        ("2025-06-01", "120"), ("2025-12-31", "145"), ("2026-08-20", "168"),
    ],
    "UNI": [
        ("2025-03-01", "9"), ("2025-12-31", "11"), ("2026-08-20", "10"),
    ],
}


def _price_eur(symbol: str, at: datetime) -> Decimal:
    curve = [(datetime.fromisoformat(d).replace(tzinfo=timezone.utc), Decimal(p)) for d, p in _PRICE_CURVES[symbol]]
    if at <= curve[0][0]:
        return curve[0][1]
    if at >= curve[-1][0]:
        return curve[-1][1]
    for (d0, p0), (d1, p1) in zip(curve, curve[1:]):
        if d0 <= at <= d1:
            span = (d1 - d0).total_seconds()
            frac = Decimal((at - d0).total_seconds() / span) if span else Decimal(0)
            return (p0 + (p1 - p0) * frac).quantize(Decimal("0.01"))
    return curve[-1][1]


def _fake_hex(seed: str, length: int) -> str:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    while len(digest) < length:
        digest += hashlib.sha256(digest.encode()).hexdigest()
    return digest[:length]


class _Event:
    """One planned demo event — deliberately explicit rather than routed
    through a connector's normalize(), since this data is synthetic and
    spans five different sources by design."""

    __slots__ = (
        "account", "ext", "event_type", "event_subtype", "direction", "asset", "amount",
        "sec_asset", "sec_amount", "at", "destination", "notes",
        "fee_asset", "fee_amount", "fee_type", "status", "provenance", "priced",
        "address_from", "address_to", "tx_hash",
    )

    def __init__(
        self, account, ext, event_type, direction, asset, amount, at, *,
        event_subtype=None, sec_asset=None, sec_amount=None, destination=None,
        notes=None, fee_asset=None, fee_amount=None, fee_type="NETWORK_FEE", status="COMPLETE",
        provenance="automatic", priced=True, address_from=None, address_to=None, tx_hash=None,
    ):
        self.account, self.ext, self.event_type, self.direction = account, ext, event_type, direction
        self.asset, self.amount, self.at = asset, amount, at
        self.event_subtype, self.sec_asset, self.sec_amount = event_subtype, sec_asset, sec_amount
        self.destination, self.notes = destination, notes
        self.fee_asset, self.fee_amount, self.fee_type = fee_asset, fee_amount, fee_type
        self.status, self.provenance, self.priced = status, provenance, priced
        self.address_from, self.address_to, self.tx_hash = address_from, address_to, tx_hash


_BTC_ADDR = "bc1q" + _fake_hex("cake-wallet", 38)
_ETH_ADDR = "0x" + _fake_hex("metamask", 40)
_SOL_ADDR = _fake_hex("phantom", 44)

# Chronological, five-source demo history (Bitget, Binance, a Bitcoin wallet,
# an EVM wallet, a Solana wallet, plus a couple of manually-entered records).
# 2024-2025 are fully reconciled and priced — a clean, complete pair of tax
# years. 2026 is deliberately left mid-flight (an unlinked transfer pair, one
# unpriced event, one unpriced cross-asset fee, two entries awaiting review)
# so the Reports readiness panel has real, non-trivial work to show.
_EVENTS: list[_Event] = [
    # --- 2024 ---------------------------------------------------------
    _Event("bitget", "bg-buy-2024-01-15", "BUY", "+", "BTC", "0.35000000", "2024-01-15T09:12:00+00:00",
           notes="Initial BTC position"),
    _Event("binance", "bn-buy-2024-01-20", "BUY", "+", "ETH", "3.00000000", "2024-01-20T14:03:00+00:00"),
    _Event("bitget", "bg-wd-2024-02-10", "WITHDRAWAL", "-", "BTC", "0.20000000", "2024-02-10T11:00:00+00:00",
           destination="Cake Wallet · Bitcoin", fee_asset="BTC", fee_amount="0.00010000",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2024-02-10", 64)),
    _Event("cake", "cake-dep-2024-02-10", "DEPOSIT", "+", "BTC", "0.19990000", "2024-02-10T11:24:00+00:00",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2024-02-10", 64)),
    _Event("binance", "bn-wd-2024-02-15", "WITHDRAWAL", "-", "ETH", "2.50000000", "2024-02-15T08:30:00+00:00",
           destination="MetaMask · Ethereum", fee_asset="ETH", fee_amount="0.00300000",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("bn-wd-2024-02-15", 64)),
    _Event("metamask", "mm-dep-2024-02-15", "DEPOSIT", "+", "ETH", "2.49700000", "2024-02-15T08:41:00+00:00",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("bn-wd-2024-02-15", 64)),
    _Event("bitget", "bg-buy-2024-04-02", "BUY", "+", "BTC", "0.15000000", "2024-04-02T10:00:00+00:00"),
    _Event("binance", "bn-buy-2024-04-10", "BUY", "+", "SOL", "40.00000000", "2024-04-10T16:20:00+00:00"),
    _Event("binance", "bn-wd-2024-04-12", "WITHDRAWAL", "-", "SOL", "35.00000000", "2024-04-12T09:00:00+00:00",
           destination="Phantom · Solana", fee_asset="SOL", fee_amount="0.01000000",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("bn-wd-2024-04-12", 64)),
    _Event("phantom", "ph-dep-2024-04-12", "DEPOSIT", "+", "SOL", "34.99000000", "2024-04-12T09:06:00+00:00",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("bn-wd-2024-04-12", 64)),
    _Event("metamask", "mm-stake-2024-05-01", "STAKING_REWARD", "+", "ETH", "0.05000000", "2024-05-01T00:00:00+00:00",
           notes="ETH2 validator reward"),
    _Event("phantom", "ph-stake-2024-06-01", "STAKING_REWARD", "+", "SOL", "0.80000000", "2024-06-01T00:00:00+00:00",
           notes="Native SOL staking reward"),
    _Event("cake", "cake-pay-2024-06-15", "PAYMENT", "-", "BTC", "0.00500000", "2024-06-15T18:45:00+00:00",
           destination="Mullvad", notes="Annual VPN subscription",
           fee_asset="BTC", fee_amount="0.00010000"),
    _Event("bitget", "bg-sell-2024-09-10", "SELL", "-", "BTC", "0.10000000", "2024-09-10T13:15:00+00:00",
           notes="Partial profit-taking"),
    _Event("binance", "bn-buy-2024-10-01", "BUY", "+", "ETH", "1.00000000", "2024-10-01T12:00:00+00:00"),
    _Event("metamask", "mm-stake-2024-11-01", "STAKING_REWARD", "+", "ETH", "0.04000000", "2024-11-01T00:00:00+00:00",
           ),
    _Event("cake", "cake-gift-2024-12-20", "GIFT_SENT", "-", "BTC", "0.01000000", "2024-12-20T10:00:00+00:00",
           notes="Birthday gift"),
    _Event("phantom", "ph-stake-2024-12-28", "STAKING_REWARD", "+", "SOL", "0.90000000", "2024-12-28T00:00:00+00:00",
           ),

    # --- 2025 (fully reconciled) ---------------------------------------
    _Event("bitget", "bg-buy-2025-01-10", "BUY", "+", "BTC", "0.10000000", "2025-01-10T09:30:00+00:00"),
    _Event("binance", "bn-sell-2025-02-05", "SELL", "-", "ETH", "1.50000000", "2025-02-05T15:00:00+00:00",
           notes="Rebalancing"),
    _Event("binance", "bn-swap-2025-03-01", "SWAP", "-", "ETH", "1.00000000", "2025-03-01T10:00:00+00:00",
           sec_asset="SOL", sec_amount="12.00000000", notes="Swapped ETH for SOL"),
    _Event("metamask", "mm-airdrop-2025-03-15", "AIRDROP", "+", "UNI", "25.00000000", "2025-03-15T00:00:00+00:00",
           notes="Governance token airdrop"),
    _Event("metamask", "mm-stake-2025-03-20", "STAKING_REWARD", "+", "ETH", "0.06000000", "2025-03-20T00:00:00+00:00",
           ),
    _Event("bitget", "bg-wd-2025-04-05", "WITHDRAWAL", "-", "BTC", "0.05000000", "2025-04-05T11:00:00+00:00",
           destination="Cake Wallet · Bitcoin", fee_asset="BTC", fee_amount="0.00010000",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2025-04-05", 64)),
    _Event("cake", "cake-dep-2025-04-05", "DEPOSIT", "+", "BTC", "0.04990000", "2025-04-05T11:19:00+00:00",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2025-04-05", 64)),
    _Event("phantom", "ph-pay-2025-04-18", "PAYMENT", "-", "SOL", "3.00000000", "2025-04-18T20:10:00+00:00",
           destination="Steam", notes="Game purchase"),
    _Event("phantom", "ph-stake-2025-05-01", "STAKING_REWARD", "+", "SOL", "1.10000000", "2025-05-01T00:00:00+00:00",
           ),
    _Event("bitget", "bg-sell-2025-05-20", "SELL", "-", "BTC", "0.08000000", "2025-05-20T14:40:00+00:00"),
    _Event("binance", "bn-buy-2025-06-02", "BUY", "+", "SOL", "20.00000000", "2025-06-02T09:00:00+00:00"),
    _Event("binance", "bn-wd-2025-06-05", "WITHDRAWAL", "-", "SOL", "18.00000000", "2025-06-05T10:00:00+00:00",
           destination="Phantom · Solana", fee_asset="SOL", fee_amount="0.05000000",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("bn-wd-2025-06-05", 64)),
    _Event("phantom", "ph-dep-2025-06-05", "DEPOSIT", "+", "SOL", "17.95000000", "2025-06-05T10:05:00+00:00",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("bn-wd-2025-06-05", 64)),
    _Event("metamask", "mm-stake-2025-06-15", "STAKING_REWARD", "+", "ETH", "0.05000000", "2025-06-15T00:00:00+00:00",
           ),
    _Event("phantom", "ph-gift-2025-07-01", "GIFT_RECEIVED", "+", "SOL", "2.00000000", "2025-07-01T00:00:00+00:00",
           notes="Gift received"),
    _Event("cake", "cake-pay-2025-07-10", "PAYMENT", "-", "BTC", "0.00300000", "2025-07-10T08:15:00+00:00",
           destination="Coffee Roasters", fee_asset="BTC", fee_amount="0.00005000"),
    _Event("binance", "bn-sell-2025-08-01", "SELL", "-", "ETH", "1.00000000", "2025-08-01T12:30:00+00:00"),
    _Event("phantom", "ph-stake-2025-08-15", "STAKING_REWARD", "+", "SOL", "1.00000000", "2025-08-15T00:00:00+00:00",
           ),
    _Event("binance", "bn-wd-2025-09-01", "WITHDRAWAL", "-", "ETH", "0.50000000", "2025-09-01T09:00:00+00:00",
           destination="MetaMask · Ethereum", fee_asset="ETH", fee_amount="0.00200000",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("bn-wd-2025-09-01", 64)),
    _Event("metamask", "mm-dep-2025-09-01", "DEPOSIT", "+", "ETH", "0.49900000", "2025-09-01T09:11:00+00:00",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("bn-wd-2025-09-01", 64)),
    _Event("metamask", "mm-stake-2025-10-01", "STAKING_REWARD", "+", "ETH", "0.07000000", "2025-10-01T00:00:00+00:00",
           ),
    _Event("phantom", "ph-wd-2025-10-10", "WITHDRAWAL", "-", "SOL", "15.00000000", "2025-10-10T13:00:00+00:00",
           destination="Binance", fee_asset="SOL", fee_amount="0.02000000",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("ph-wd-2025-10-10", 64)),
    _Event("binance", "bn-dep-2025-10-10", "DEPOSIT", "+", "SOL", "14.95000000", "2025-10-10T13:07:00+00:00",
           address_to=_SOL_ADDR, tx_hash=_fake_hex("ph-wd-2025-10-10", 64)),
    _Event("binance", "bn-sell-2025-10-12", "SELL", "-", "SOL", "14.00000000", "2025-10-12T15:00:00+00:00"),
    _Event("phantom", "ph-stake-2025-11-01", "STAKING_REWARD", "+", "SOL", "0.90000000", "2025-11-01T00:00:00+00:00",
           ),
    _Event("bitget", "bg-buy-2025-11-15", "BUY", "+", "BTC", "0.06000000", "2025-11-15T10:00:00+00:00"),
    _Event("manual", "manual-2025-12-05", "MANUAL_ADJUSTMENT", "+", "BTC", "0.00200000", "2025-12-05T00:00:00+00:00",
           provenance="manual", notes="Recovered from an old paper wallet, verified against the printed key"),
    _Event("metamask", "mm-stake-2025-12-20", "STAKING_REWARD", "+", "ETH", "0.05000000", "2025-12-20T00:00:00+00:00",
           ),

    # --- 2026 (in progress — deliberately not fully reconciled) --------
    _Event("bitget", "bg-buy-2026-01-14", "BUY", "+", "BTC", "0.01240000", "2026-01-14T10:32:00+00:00"),
    _Event("bitget", "bg-buy-2026-02-02", "BUY", "+", "BTC", "0.00480000", "2026-02-02T15:05:00+00:00"),
    # Deliberately left unlinked: this is the pair the Reports readiness
    # panel should flag with a "likely match" link suggestion.
    _Event("bitget", "bg-wd-2026-02-18", "WITHDRAWAL", "-", "BTC", "0.00600000", "2026-02-18T07:41:00+00:00",
           destination="Cake Wallet · Bitcoin", fee_asset="BTC", fee_amount="0.00001000",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2026-02-18", 64)),
    _Event("cake", "cake-dep-2026-02-18", "DEPOSIT", "+", "BTC", "0.00599000", "2026-02-18T09:14:00+00:00",
           address_to=_BTC_ADDR, tx_hash=_fake_hex("bg-wd-2026-02-18", 64)),
    _Event("cake", "cake-pay-2026-03-01", "PAYMENT", "-", "BTC", "0.00020000", "2026-03-01T19:20:00+00:00",
           destination="Mullvad", notes="Monthly service payment",
           fee_asset="BTC", fee_amount="0.00000100"),
    _Event("metamask", "mm-stake-2026-05-10", "STAKING_REWARD", "+", "ETH", "0.03000000", "2026-05-10T00:00:00+00:00",
           ),
    # Cross-asset gas fee (ERC-20 transfer, gas paid in ETH) with no cached
    # ETH price nearby — the "fees may be incomplete" readiness check.
    _Event("metamask", "mm-wd-2026-06-01", "WITHDRAWAL", "-", "UNI", "10.00000000", "2026-06-01T12:00:00+00:00",
           destination="Binance", fee_asset="ETH", fee_amount="0.00200000",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("mm-wd-2026-06-01", 64)),
    _Event("binance", "bn-dep-2026-06-01", "DEPOSIT", "+", "UNI", "9.90000000", "2026-06-01T12:09:00+00:00",
           address_to=_ETH_ADDR, tx_hash="0x" + _fake_hex("mm-wd-2026-06-01", 64)),
    # Deliberately unpriced — the "missing prices" readiness check.
    _Event("binance", "bn-buy-2026-06-10", "BUY", "+", "SOL", "8.00000000", "2026-06-10T09:00:00+00:00", priced=False),
    _Event("bitget", "bg-sell-2026-07-15", "SELL", "-", "BTC", "0.03000000", "2026-07-15T14:00:00+00:00"),
    _Event("manual", "manual-2026-08-20a", "MANUAL_ADJUSTMENT", "+", "BTC", "0.00500000", "2026-08-20T14:00:00+00:00",
           status="REQUIRES_REVIEW", provenance="manual", notes="Pending confirmation from cold storage export"),
    _Event("manual", "manual-2026-08-20b", "MANUAL_ADJUSTMENT", "+", "BTC", "0.00010000", "2026-08-20T14:34:00+00:00",
           status="REQUIRES_REVIEW", provenance="manual", notes="Pending confirmation from cold storage export"),
]

# (event external_id, field, old_value, new_value, reason) — a couple of
# manual corrections so the tax report's "Manual corrections" section and
# the event audit trail have something real to show.
_OVERRIDES = [
    ("mm-airdrop-2025-03-15", "notes", "", "Governance token airdrop", "Documented the source after checking Etherscan", "2025-03-18T20:00:00+00:00"),
]


def seed_demo(session: Session) -> None:
    if session.query(Account).count() > 0:
        return

    accounts = _create_accounts(session)
    events_by_ext: dict[str, Event] = {}

    for planned in _EVENTS:
        event = _create_event(session, planned, accounts)
        events_by_ext[planned.ext] = event

    # Reconciled transfers: link every WITHDRAWAL/DEPOSIT pair except the
    # one intentionally left for the readiness panel to flag.
    _link(session, events_by_ext, "bg-wd-2024-02-10", "cake-dep-2024-02-10")
    _link(session, events_by_ext, "bn-wd-2024-02-15", "mm-dep-2024-02-15")
    _link(session, events_by_ext, "bn-wd-2024-04-12", "ph-dep-2024-04-12")
    _link(session, events_by_ext, "bg-wd-2025-04-05", "cake-dep-2025-04-05")
    _link(session, events_by_ext, "bn-wd-2025-06-05", "ph-dep-2025-06-05")
    _link(session, events_by_ext, "bn-wd-2025-09-01", "mm-dep-2025-09-01")
    _link(session, events_by_ext, "ph-wd-2025-10-10", "bn-dep-2025-10-10")
    _link(session, events_by_ext, "mm-wd-2026-06-01", "bn-dep-2026-06-01")

    # The deliberately-unlinked 2026 BTC pair: run the real matcher so it
    # surfaces the same "possible internal transfer" issue a live sync would.
    match_transfers(session, events_by_ext["bg-wd-2026-02-18"])
    match_transfers(session, events_by_ext["cake-dep-2026-02-18"])

    for ext, field, old, new, reason, changed_at in _OVERRIDES:
        event = events_by_ext[ext]
        session.add(Override(event_id=event.id, field=field, old_value=old or None, new_value=new, reason=reason, changed_at=datetime.fromisoformat(changed_at)))

    session.add_all([
        SyncState(source_id="bitget", last_sync=datetime.now(timezone.utc), status="ok", records_imported=sum(1 for e in _EVENTS if e.account == "bitget")),
        SyncState(source_id="binance", last_sync=datetime.now(timezone.utc), status="ok", records_imported=sum(1 for e in _EVENTS if e.account == "binance")),
    ])

    settings = session.get(AppSettings, 1) or AppSettings(id=1)
    settings.taxpayer_name = "Alex Novak"
    settings.default_country = "ES"
    settings.default_tax_year = 2025
    settings.default_language = "en"
    session.add(settings)

    session.commit()


def _create_accounts(session: Session) -> dict[str, Account]:
    def demo_config() -> str | None:
        try:
            return encrypt_config({"api_key": "demo-" + _fake_hex("api-key", 16), "api_secret": "demo-secret-redacted"})
        except RuntimeError:
            return None  # no encryption key configured in this environment — skip, don't crash startup

    now = datetime.now(timezone.utc)
    # paused=True on every syncable demo account: it keeps the background
    # sync loop from immediately re-syncing them against fake credentials
    # and flipping them to an "error" status a few minutes into the demo.
    # The imported history stays fully intact and visible either way.
    accounts = {
        "bitget": Account(
            name="Bitget", kind="exchange", connector_type="bitget_live", status="connected", paused=True,
            config_encrypted=demo_config(), note="Read-only API key, spot trading history · auto-sync paused for this demo", last_sync=now,
        ),
        "binance": Account(
            name="Binance", kind="exchange", connector_type="binance_live", status="connected", paused=True,
            config_encrypted=demo_config(), note="Read-only API key, spot trading history · auto-sync paused for this demo", last_sync=now,
        ),
        "cake": Account(
            name="Cake Wallet · Bitcoin", kind="wallet", connector_type="bitcoin_address", status="connected", paused=True,
            address=_BTC_ADDR, wallet_software="Cake Wallet", note="Personal cold-ish wallet · auto-sync paused for this demo", last_sync=now,
        ),
        "metamask": Account(
            name="MetaMask · Ethereum", kind="wallet", connector_type="evm_address", chain_network="ethereum", paused=True,
            status="connected", address=_ETH_ADDR, wallet_software="MetaMask", note="Browser wallet · auto-sync paused for this demo", last_sync=now,
        ),
        "phantom": Account(
            name="Phantom · Solana", kind="wallet", connector_type="manual", status="connected", paused=True,
            address=_SOL_ADDR, wallet_software="Phantom", note="Tracked by hand · no live Solana sync in this demo", last_sync=now,
        ),
        "manual": Account(name="Manual entries", kind="manual", connector_type="manual", status="connected"),
    }
    session.add_all(accounts.values())
    session.flush()
    return accounts


def _create_event(session: Session, planned: _Event, accounts: dict[str, Account]) -> Event:
    at = datetime.fromisoformat(planned.at)
    payload = {
        "id": planned.ext,
        "type": planned.event_type,
        "asset": planned.asset,
        "amount": planned.amount,
        "timestamp": planned.at,
        "notes": planned.notes,
        "tx_hash": planned.tx_hash,
    }
    raw = RawRecord(planned.account, planned.ext, at, payload)
    raw_row = store_raw(session, raw, "demo-1.0")

    asset = get_or_create_asset(session, planned.asset)
    secondary_asset = get_or_create_asset(session, planned.sec_asset) if planned.sec_asset else None
    account = accounts[planned.account]

    event = Event(
        external_id=f"{planned.account}:{planned.ext}",
        raw_event_id=raw_row.id if raw_row else None,
        account_id=account.id,
        event_type=planned.event_type,
        event_subtype=planned.event_subtype,
        direction=planned.direction,
        status=planned.status,
        occurred_at=at,
        primary_asset_id=asset.id,
        primary_amount=planned.amount,
        secondary_asset_id=secondary_asset.id if secondary_asset else None,
        secondary_amount=planned.sec_amount,
        address_from=planned.address_from,
        address_to=planned.address_to or planned.destination or (account.name if planned.event_type == "DEPOSIT" else None),
        tx_hash=planned.tx_hash,
        provenance=planned.provenance,
        normalizer_version="demo-1.0",
    )
    session.add(event)
    session.flush()

    if planned.fee_asset and planned.fee_amount:
        fee_asset = get_or_create_asset(session, planned.fee_asset)
        session.add(Fee(event_id=event.id, fee_type=planned.fee_type, fee_asset_id=fee_asset.id, fee_amount=planned.fee_amount))

    if planned.priced:
        _seed_valuation(session, event, asset, at)

    return event


def _seed_valuation(session: Session, event: Event, asset: Asset, at: datetime) -> None:
    if asset.asset_type == "FIAT":
        return
    eur_rate = _price_eur(asset.symbol, at) if asset.symbol in _PRICE_CURVES else Decimal("1")
    amount = Decimal(event.primary_amount)
    for currency, rate in (("EUR", eur_rate), ("SEK", eur_rate * _SEK_PER_EUR)):
        session.add(
            Valuation(
                event_id=event.id,
                quote_currency=currency,
                unit_price=str(rate.quantize(Decimal("0.01"))),
                total_value=str((amount * rate).quantize(Decimal("0.01"))),
                requested_timestamp=at,
                observation_timestamp=at,
                provider="demo",
                provider_asset_id=asset.coingecko_id or asset.symbol.lower(),
                method="MANUAL",
                confidence="low",
            )
        )


def _link(session: Session, events_by_ext: dict[str, Event], from_ext: str, to_ext: str) -> None:
    session.add(
        EventLink(
            event_id=events_by_ext[from_ext].id,
            linked_event_id=events_by_ext[to_ext].id,
            relationship_type="INTERNAL_TRANSFER",
            provenance="automatic",
            confidence="high",
        )
    )
