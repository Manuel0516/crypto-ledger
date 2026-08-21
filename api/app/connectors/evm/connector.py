from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

# The existing chains use their public Blockscout Etherscan-compatible APIs.
# Routescan provides the same Etherscan-compatible request shape for BNB Smart
# Chain on its free keyless tier, so BSC can remain a public-address-only
# source instead of gaining exchange-style credentials.
CHAINS: dict[str, tuple[str, str]] = {
    "ethereum": ("https://eth.blockscout.com/api", "Ethereum"),
    "polygon": ("https://polygon.blockscout.com/api", "Polygon"),
    "arbitrum": ("https://arbitrum.blockscout.com/api", "Arbitrum"),
    "optimism": ("https://optimism.blockscout.com/api", "Optimism"),
    "base": ("https://base.blockscout.com/api", "Base"),
    "bsc": ("https://api.routescan.io/v2/network/mainnet/evm/56/etherscan/api", "BNB Smart Chain"),
}

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class EVMAddressConnector:
    """Read-only EVM address tracking. Covers native transfers, ERC-20,
    ERC-721, and ERC-1155 transfers (plan §33). A zero-value transaction that
    calls a contract (a DEX swap, a staking deposit, a bridge, any DeFi
    interaction we don't specifically decode) still becomes a canonical
    UNKNOWN event carrying its real gas cost and tx hash, rather than being
    silently dropped — the specific protocol isn't decoded, but the fact
    that something happened, and what it cost, is never lost (plan §38)."""

    source_id = "evm"

    def __init__(self, address: str, account_label: str, chain: str = "ethereum"):
        self.address = address.lower()
        self.account_label = account_label
        self.chain = chain if chain in CHAINS else "ethereum"

    @property
    def version(self) -> str:
        return "evm-address-0.4"

    def _get_raw(self, base: str, params: dict):
        try:
            response = httpx.get(base, params=params, timeout=15.0)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Could not reach {base}: {exc}") from exc
        if data.get("message") not in ("OK", "No transactions found"):
            raise ConnectorUnavailable(f"Unexpected response from {base}: {data.get('message')}")
        return data.get("result")

    def _get(self, base: str, params: dict) -> list[dict]:
        return self._get_raw(base, params) or []

    PAGE_SIZE = 50
    BACKFILL_PAGES = 5  # since=None (first sync) -> up to 250 records per action

    def _paged(self, base: str, action: str, pages: int) -> Iterable[dict]:
        for page in range(1, pages + 1):
            rows = self._get(
                base,
                {
                    "module": "account",
                    "action": action,
                    "address": self.address,
                    "sort": "desc",
                    "page": str(page),
                    "offset": str(self.PAGE_SIZE),
                },
            )
            yield from rows
            if len(rows) < self.PAGE_SIZE:
                return

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        base, network = CHAINS[self.chain]
        namespace = f"{self.source_id}:{self.chain}:{self.address}"
        pages = self.BACKFILL_PAGES if since is None else 1

        for tx in self._paged(base, "txlist", pages):
            if tx.get("isError") == "1":
                continue
            has_value = tx.get("value", "0") != "0"
            is_contract_call = (tx.get("input") or "0x") != "0x"
            if not has_value and not is_contract_call:
                continue  # a plain no-op self-call with nothing to record
            kind = "native" if has_value else "contract_call"
            payload = {**tx, "_kind": kind, "_network": network}
            yield RawRecord(namespace, tx["hash"], _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged(base, "tokentx", pages):
            payload = {**tx, "_kind": "token", "_network": network}
            external_id = f"{tx['hash']}-token-{tx.get('contractAddress', '')}-{tx.get('logIndex', '0')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged(base, "tokennfttx", pages):
            payload = {**tx, "_kind": "nft721", "_network": network}
            external_id = f"{tx['hash']}-nft721-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged(base, "token1155tx", pages):
            payload = {**tx, "_kind": "nft1155", "_network": network}
            external_id = f"{tx['hash']}-nft1155-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

    def fetch_balances(self) -> Iterable[Balance]:
        base, network = CHAINS[self.chain]
        balances: list[Balance] = []

        native_wei = self._get_raw(base, {"module": "account", "action": "balance", "address": self.address})
        try:
            native_amount = int(native_wei) / 1e18
        except (TypeError, ValueError):
            native_amount = 0
        if native_amount:
            balances.append(Balance(_native_symbol(network), f"{native_amount:.18f}", asset_network=network))

        # "tokenlist" isn't universally supported across every Etherscan-
        # compatible explorer (Routescan's BSC endpoint in particular) — a
        # missing token list just means "no token balances reported", not a
        # failed reconciliation; the native balance above still stands.
        try:
            tokens = self._get_raw(base, {"module": "account", "action": "tokenlist", "address": self.address}) or []
        except ConnectorUnavailable:
            tokens = []
        for token in tokens if isinstance(tokens, list) else []:
            if token.get("type") not in (None, "ERC-20"):
                continue  # skip NFTs here — a quantity-of-1 balance isn't a meaningful reconciliation figure
            try:
                decimals = int(token.get("decimals") or 18)
                raw_balance = int(token.get("balance", "0"))
            except (TypeError, ValueError):
                continue
            if raw_balance == 0:
                continue
            balances.append(
                Balance(
                    token.get("symbol") or "UNKNOWN",
                    f"{raw_balance / (10 ** decimals):.{min(decimals, 18)}f}",
                    asset_network=network,
                    asset_contract=token.get("contractAddress"),
                )
            )
        return balances

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        kind = payload["_kind"]
        is_incoming = payload.get("to", "").lower() == self.address

        if kind == "contract_call":
            gas_eth = int(payload["gasUsed"]) * int(payload["gasPrice"]) / 1e18
            return NormalizedEvent(
                event_type="UNKNOWN",
                event_subtype="contract_call",
                direction="-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=_native_symbol(payload["_network"]),
                asset_network=payload["_network"],
                amount="0",
                source_label=self.account_label,
                counterparty=payload.get("to"),
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=f"Contract call, not decoded · {payload['hash'][:12]}…",
                fees=[NormalizedFee(fee_type="GAS_FEE", asset_symbol=_native_symbol(payload["_network"]), amount=f"{gas_eth:.18f}")],
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
                contract_address=payload.get("to"),
            )

        if kind == "native":
            event_type, direction = ("DEPOSIT", "+") if is_incoming else ("WITHDRAWAL", "-")
            amount = int(payload["value"]) / 1e18
            fees: list[NormalizedFee] = []
            if not is_incoming:
                gas_eth = int(payload["gasUsed"]) * int(payload["gasPrice"]) / 1e18
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=_native_symbol(payload["_network"]), amount=f"{gas_eth:.18f}"))
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="native",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=_native_symbol(payload["_network"]),
                asset_network=payload["_network"],
                amount=f"{amount:.18f}",
                source_label=self.account_label,
                counterparty=payload.get("from") if is_incoming else payload.get("to"),
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=f"On-chain tx {payload['hash'][:12]}…",
                fees=fees,
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
            )

        if kind == "token":
            event_type, direction = ("DEPOSIT", "+") if is_incoming else ("WITHDRAWAL", "-")
            decimals = int(payload.get("tokenDecimal") or 18)
            amount = int(payload["value"]) / (10**decimals)
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="token",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=payload.get("tokenSymbol") or "UNKNOWN",
                asset_network=payload["_network"],
                asset_contract=payload.get("contractAddress"),
                asset_type="TOKEN",
                amount=f"{amount:.{min(decimals, 18)}f}",
                source_label=self.account_label,
                counterparty=payload.get("from") if is_incoming else payload.get("to"),
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=f"{payload.get('tokenName') or 'Token'} transfer {payload['hash'][:12]}…",
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
                log_index=_maybe_int(payload.get("logIndex")),
                contract_address=payload.get("contractAddress"),
            )

        # nft721 / nft1155
        is_mint = payload.get("from", "").lower() == _ZERO_ADDRESS
        event_type = "NFT_MINT" if is_mint else ("NFT_TRANSFER")
        direction = "+" if is_incoming else "-"
        quantity = payload.get("tokenValue", "1") if kind == "nft1155" else "1"
        token_id = payload.get("tokenID", "")
        standard = "ERC-1155" if kind == "nft1155" else "ERC-721"
        return NormalizedEvent(
            event_type=event_type,
            event_subtype=kind,
            direction=direction,
            status="COMPLETE",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol=payload.get("tokenSymbol") or "NFT",
            asset_network=payload["_network"],
            asset_contract=payload.get("contractAddress"),
            asset_type="NFT",
            amount=str(quantity),
            source_label=self.account_label,
            counterparty=payload.get("from") if is_incoming else payload.get("to"),
            address_from=payload.get("from"),
            address_to=payload.get("to"),
            notes=f"{standard} {payload.get('tokenName') or 'NFT'} #{token_id} · {payload['hash'][:12]}…",
            tx_hash=payload.get("hash"),
            block_height=_maybe_int(payload.get("blockNumber")),
            block_hash=payload.get("blockHash"),
            log_index=_maybe_int(payload.get("logIndex")),
            contract_address=payload.get("contractAddress"),
        )


def _timestamp(value: str) -> datetime:
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _maybe_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _native_symbol(network: str) -> str:
    return {"Polygon": "MATIC", "BNB Smart Chain": "BNB"}.get(network, "ETH")
