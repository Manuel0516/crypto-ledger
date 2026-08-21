from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

from .hd import InvalidExtendedKey, derive_addresses

MEMPOOL_BASE = "https://mempool.space/api"
EXTENDED_KEY_PREFIXES = ("xpub", "ypub", "zpub")
_REQUEST_PACING_SECONDS = 0.3  # gap-limit scanning can make dozens of calls; be a good API citizen
_MAX_429_RETRIES = 3


def looks_like_extended_key(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in EXTENDED_KEY_PREFIXES)


def _get_json(path: str) -> dict | list:
    time.sleep(_REQUEST_PACING_SECONDS)
    backoff = 2.0
    try:
        for attempt in range(_MAX_429_RETRIES + 1):
            response = httpx.get(f"{MEMPOOL_BASE}{path}", timeout=15.0)
            if response.status_code != 429:
                break
            if attempt == _MAX_429_RETRIES:
                raise ConnectorUnavailable("mempool.space is rate-limiting this host — try again in a few minutes")
            time.sleep(backoff)
            backoff *= 2
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise ConnectorUnavailable(f"Could not reach mempool.space: {exc}") from exc


def _address_balance_btc(address: str) -> str:
    data = _get_json(f"/address/{address}")
    if not isinstance(data, dict):
        return "0"
    chain = data.get("chain_stats", {})
    sats = chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)
    return f"{sats / 1e8:.8f}"


def _fetch_page(address: str, last_seen_txid: str | None) -> list[dict]:
    path = f"/address/{address}/txs"
    if last_seen_txid:
        path += f"/chain/{last_seen_txid}"
    result = _get_json(path)
    return result if isinstance(result, list) else []


def _net_value(tx: dict, own_addresses: set[str]) -> int:
    value_in = sum(v["prevout"]["value"] for v in tx["vin"] if v.get("prevout", {}).get("scriptpubkey_address") in own_addresses)
    value_out = sum(v["value"] for v in tx["vout"] if v.get("scriptpubkey_address") in own_addresses)
    return value_out - value_in


def _vin_address(entry: dict) -> str | None:
    return entry.get("prevout", {}).get("scriptpubkey_address")


def _vout_address(entry: dict) -> str | None:
    return entry.get("scriptpubkey_address")


def _first_address(entries: list[dict], get: callable, own_addresses: set[str], *, external: bool) -> str | None:
    """First counterparty (or own) address on one side of the tx — a BTC tx
    can have many inputs/outputs, so this is a reasonable single
    representative rather than a complete list (plan §17)."""
    for entry in entries:
        address = get(entry)
        if not address:
            continue
        if (address not in own_addresses) == external:
            return address
    return None


def _tx_to_event(tx: dict, own_addresses: set[str], source_label: str, occurred_at: datetime) -> NormalizedEvent:
    net_sats = _net_value(tx, own_addresses)
    value_in = sum(v["prevout"]["value"] for v in tx["vin"] if v.get("prevout", {}).get("scriptpubkey_address") in own_addresses)

    fees: list[NormalizedFee] = []
    if net_sats >= 0:
        event_type, direction = "DEPOSIT", "+"
        address_from = _first_address(tx["vin"], _vin_address, own_addresses, external=True)
        address_to = _first_address(tx["vout"], _vout_address, own_addresses, external=False)
    else:
        event_type, direction = "WITHDRAWAL", "-"
        address_from = _first_address(tx["vin"], _vin_address, own_addresses, external=False)
        address_to = _first_address(tx["vout"], _vout_address, own_addresses, external=True)
        if value_in > 0 and tx.get("fee"):
            fees.append(NormalizedFee(fee_type="NETWORK_FEE", asset_symbol="BTC", amount=f"{tx['fee'] / 1e8:.8f}"))

    status = tx.get("status", {})
    return NormalizedEvent(
        event_type=event_type,
        event_subtype="onchain",
        direction=direction,
        status="COMPLETE",
        occurred_at=occurred_at,
        original_timestamp=occurred_at.isoformat(),
        asset_symbol="BTC",
        asset_network="Bitcoin",
        amount=f"{abs(net_sats) / 1e8:.8f}",
        source_label=source_label,
        address_from=address_from,
        address_to=address_to,
        notes=f"On-chain tx {tx['txid'][:12]}…",
        fees=fees,
        tx_hash=tx.get("txid"),
        block_height=status.get("block_height"),
        block_hash=status.get("block_hash"),
    )


class BitcoinAddressConnector:
    """Read-only, single-address Bitcoin tracking via a public block explorer
    API. No credentials, no seed/xprv — only ever a watch address (plan
    §32/§81). Use BitcoinXpubConnector for a whole HD wallet instead of one
    address at a time."""

    source_id = "bitcoin"

    def __init__(self, address: str, account_label: str):
        self.address = address
        self.account_label = account_label

    @property
    def version(self) -> str:
        return "bitcoin-address-0.2"

    # since=None means "first sync for this account" -> pull deeper history.
    # since=<timestamp> means "routine check" -> the most recent page is
    # enough; idempotent raw-evidence storage makes re-checking it safe.
    BACKFILL_PAGES = 10

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        pages = self.BACKFILL_PAGES if since is None else 1
        last_seen_txid: str | None = None

        for _ in range(pages):
            txs = _fetch_page(self.address, last_seen_txid)
            if not txs:
                break
            for tx in txs:
                status = tx.get("status", {})
                if not status.get("confirmed"):
                    continue  # wait for confirmation so the event gets a stable timestamp
                source_timestamp = datetime.fromtimestamp(status["block_time"], tz=timezone.utc)
                yield RawRecord(f"{self.source_id}:{self.address}", tx["txid"], source_timestamp, tx)
            confirmed = [tx for tx in txs if tx.get("status", {}).get("confirmed")]
            if len(confirmed) < 25:
                break  # short page means we've reached the end of history
            last_seen_txid = confirmed[-1]["txid"]

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        return _tx_to_event(raw.payload, {self.address}, self.account_label, occurred_at)

    def fetch_balances(self) -> Iterable[Balance]:
        return [Balance("BTC", _address_balance_btc(self.address), asset_network="Bitcoin")]


class BitcoinXpubConnector:
    """Watches an entire HD wallet from its public extended key (xpub/ypub/
    zpub) instead of one address — matches how real wallets actually work
    (a fresh receive address per transaction). Standard BIP44/49/84 gap-limit
    scanning: derived addresses are checked in batches of GAP_LIMIT, stopping
    once a whole batch shows no on-chain activity at all. Never sees a seed
    or private key — only public-key derivation (plan §32/§81)."""

    source_id = "bitcoin"
    GAP_LIMIT = 20
    MAX_BATCHES = 5  # hard cap: up to 100 addresses per chain per sync

    def __init__(self, xkey: str, account_label: str):
        self.xkey = xkey
        self.account_label = account_label
        self._own_addresses: set[str] = set()

    @property
    def version(self) -> str:
        return "bitcoin-xpub-0.1"

    def _fingerprint(self) -> str:
        return hashlib.sha256(self.xkey.encode()).hexdigest()[:16]

    def _address_active(self, address: str) -> bool:
        data = _get_json(f"/address/{address}")
        if not isinstance(data, dict):
            return False
        chain = data.get("chain_stats", {})
        mempool = data.get("mempool_stats", {})
        return (chain.get("tx_count", 0) + mempool.get("tx_count", 0)) > 0

    def _scan_chain(self, change: bool, max_batches: int) -> list[str]:
        used: list[str] = []
        for batch in range(max_batches):
            candidates = derive_addresses(self.xkey, self.GAP_LIMIT, change=change, start=batch * self.GAP_LIMIT)
            batch_used = [addr for addr in candidates if self._address_active(addr)]
            used.extend(batch_used)
            if not batch_used:
                break
        return used

    def test_connection(self) -> bool:
        derive_addresses(self.xkey, 1)  # raises InvalidExtendedKey if malformed
        return True

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        # A full deep discovery scan (up to MAX_BATCHES) only runs on
        # backfill. Routine incremental checks only look at the first
        # GAP_LIMIT addresses per chain — enough to notice a wallet has
        # started using addresses beyond what backfill already found,
        # without re-verifying dozens of already-known addresses against
        # mempool.space on every scheduled tick.
        max_batches = self.MAX_BATCHES if since is None else 1
        try:
            addresses = self._scan_chain(change=False, max_batches=max_batches) + self._scan_chain(change=True, max_batches=max_batches)
        except InvalidExtendedKey as exc:
            raise ConnectorUnavailable(str(exc)) from exc
        self._own_addresses = set(addresses)

        pages = BitcoinAddressConnector.BACKFILL_PAGES if since is None else 1
        seen_txids: set[str] = set()
        namespace = f"{self.source_id}:xpub:{self._fingerprint()}"

        for address in addresses:
            last_seen_txid: str | None = None
            for _ in range(pages):
                txs = _fetch_page(address, last_seen_txid)
                if not txs:
                    break
                for tx in txs:
                    status = tx.get("status", {})
                    if not status.get("confirmed") or tx["txid"] in seen_txids:
                        continue
                    seen_txids.add(tx["txid"])
                    source_timestamp = datetime.fromtimestamp(status["block_time"], tz=timezone.utc)
                    yield RawRecord(namespace, tx["txid"], source_timestamp, tx)
                confirmed = [tx for tx in txs if tx.get("status", {}).get("confirmed")]
                if len(confirmed) < 25:
                    break
                last_seen_txid = confirmed[-1]["txid"]

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        return _tx_to_event(raw.payload, self._own_addresses, self.account_label, occurred_at)

    def fetch_balances(self) -> Iterable[Balance]:
        try:
            addresses = self._scan_chain(change=False, max_batches=self.MAX_BATCHES) + self._scan_chain(change=True, max_batches=self.MAX_BATCHES)
        except InvalidExtendedKey as exc:
            raise ConnectorUnavailable(str(exc)) from exc
        total_sats = Decimal(0)
        for address in addresses:
            total_sats += Decimal(_address_balance_btc(address))
        return [Balance("BTC", format(total_sats, "f"), asset_network="Bitcoin")]
