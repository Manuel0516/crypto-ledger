from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord
from app.connectors.evm import bsc_rpc

# An EVM address is not enough to identify a chain: the same 0x address can
# exist on every EVM network. Keep built-in networks explicit and never map an
# unknown value back to Ethereum.
#
# Blockscout exposes a public Etherscan-compatible API for the first group.
# Routescan indexes Avalanche, but does not index BSC. BSC is therefore wired
# to Etherscan V2, which needs one user-supplied API key. Custom EVM networks
# use the same Etherscan-compatible shape by default and can override the
# endpoint in the encrypted account config.
CHAINS: dict[str, tuple[str, str]] = {
    "ethereum": ("https://eth.blockscout.com/api", "Ethereum"),
    "polygon": ("https://polygon.blockscout.com/api", "Polygon"),
    "arbitrum": ("https://arbitrum.blockscout.com/api", "Arbitrum"),
    "optimism": ("https://optimism.blockscout.com/api", "Optimism"),
    "base": ("https://base.blockscout.com/api", "Base"),
    "bsc": ("https://api.etherscan.io/v2/api", "BNB Smart Chain"),
    "avalanche": ("https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api", "Avalanche"),
}

CHAIN_IDS: dict[str, str] = {
    "ethereum": "1",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "base": "8453",
    "bsc": "56",
    "avalanche": "43114",
}

# Tracked automatically for every BSC account that isn't using an Etherscan
# key (see EVMAddressConnector._bsc_token_contracts) — the handful of
# BEP-20 tokens most BSC wallets actually hold, so a new account already
# reports USDT/USDC/etc. without the user having to look up contract
# addresses first. Each address was confirmed on-chain (symbol()/decimals()
# read back as expected) before being hardcoded here.
_DEFAULT_BSC_TOKEN_CONTRACTS: tuple[str, ...] = (
    "0x55d398326f99059fF775485246999027B3197955",  # USDT (Binance-Peg BSC-USD)
    "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",  # USDC
    "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",  # BUSD (Binance-Peg)
    "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c",  # BTCB (Binance-Peg Bitcoin)
    "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",  # ETH (Binance-Peg Ethereum)
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB (Wrapped BNB — distinct from native BNB)
)

_CUSTOM_CHAIN = "custom"
_ETH_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")
_OPTIONAL_ACTIONS = {"tokentx", "tokennfttx", "token1155tx", "tokenlist"}
_UNSUPPORTED_MARKERS = ("not supported", "unsupported", "not available", "unknown action", "invalid action")
_EMPTY_MARKERS = (
    "no transactions found",
    "no token transfers found",
    "no nft transfers found",
    "no erc20 transfers found",
    "no erc721 transfers found",
    "no erc1155 transfers found",
    "no erc-1155 transfers found",
    "no records found",
    "no results found",
    "no data found",
)

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class _ExplorerResponseError(ConnectorUnavailable):
    """An Etherscan-compatible response that was valid HTTP/JSON but failed."""

    def __init__(self, base: str, action: str, message: str):
        self.action = action
        self.response_message = message
        action_note = f" while requesting {action}" if action else ""
        super().__init__(f"Unexpected response from {base}{action_note}: {message}")


class EVMAddressConnector:
    """Read-only EVM address tracking.

    Covers native transfers, ERC-20, ERC-721, and ERC-1155 transfers. A
    zero-value contract call remains a canonical UNKNOWN event carrying its
    gas cost and transaction hash, rather than being silently dropped.
    """

    source_id = "evm"

    def __init__(self, address: str, account_label: str, chain: str = "ethereum", config: dict | None = None):
        self.address = (address or "").lower()
        self.account_label = account_label
        self.chain = chain
        self.config = config or {}

    @property
    def version(self) -> str:
        return "evm-address-0.5"

    def _network_config(self) -> tuple[str, str, str, str | None, str]:
        if self.chain in CHAINS:
            base, network = CHAINS[self.chain]
            chain_id = CHAIN_IDS[self.chain]
            native_symbol = _native_symbol(network)
        elif self.chain == _CUSTOM_CHAIN:
            chain_id = str(self.config.get("chain_id") or "").strip()
            network = str(self.config.get("network_name") or "").strip()
            native_symbol = str(self.config.get("native_symbol") or "ETH").strip().upper()
            if not chain_id.isdigit() or int(chain_id) <= 0:
                raise ConnectorUnavailable("Custom EVM network needs a positive numeric chain ID.")
            if not network:
                raise ConnectorUnavailable("Custom EVM network needs a name.")
            base = str(self.config.get("explorer_api_url") or "").strip()
            if not base:
                base = f"https://api.routescan.io/v2/network/mainnet/evm/{chain_id}/etherscan/api"
            if not base.startswith(("https://", "http://")):
                raise ConnectorUnavailable("Custom EVM explorer API URL must start with http:// or https://.")
        else:
            raise ConnectorUnavailable(
                f"Unsupported EVM network '{self.chain}'. Select a supported network or configure it as a custom EVM network."
            )

        api_key = str(self.config.get("explorer_api_key") or "").strip() or None
        return base, network, chain_id, api_key, native_symbol

    def _bsc_public_rpc_mode(self) -> bool:
        """True when this account tracks BSC without an Etherscan key. There
        is no free, keyless, indexed history API for BSC (Routescan doesn't
        cover it; no Blockscout instance does either) — this mode falls
        back to free public BSC RPC nodes instead: a live native BNB
        balance always, plus real transfer history for the specific
        BEP-20 contracts in _bsc_token_contracts() (see bsc_rpc.py). Native
        BNB sends/receives and any token not in that list stay invisible —
        a raw node has no per-address transaction index, only per-contract
        log scanning."""
        _base, _network, _chain_id, api_key, _native = self._network_config()
        return self.chain == "bsc" and not api_key

    def _bsc_token_contracts(self) -> list[str]:
        """Contracts to track for a keyless BSC account: the common tokens
        in _DEFAULT_BSC_TOKEN_CONTRACTS, plus anything the user added, so a
        new account already tracks USDT/USDC/etc. without configuration."""
        raw = self.config.get("bsc_token_contracts") or []
        if isinstance(raw, str):
            raw = [raw]
        contracts = [str(c).strip() for c in raw if str(c).strip()]
        invalid = [c for c in contracts if not _ETH_ADDRESS.fullmatch(c)]
        if invalid:
            raise ConnectorUnavailable(f"Invalid BEP-20 contract address: {invalid[0]}")
        seen = {c.lower() for c in contracts}
        return contracts + [d for d in _DEFAULT_BSC_TOKEN_CONTRACTS if d.lower() not in seen]

    def _get_raw(self, base: str, params: dict):
        request_params = dict(params)
        _configured_base, _network, chain_id, api_key, _native = self._network_config()
        action = str(request_params.get("action") or "")
        # Etherscan V2 and Routescan's Etherscan-compatible endpoint use a
        # chain ID. Blockscout instance URLs are already chain-specific.
        configured_style = str(self.config.get("explorer_api_style") or "").lower()
        if self.chain == _CUSTOM_CHAIN and not configured_style:
            configured_style = "blockscout" if "blockscout" in base.lower() else "etherscan" if "etherscan" in base.lower() else "routescan"
        custom_blockscout = self.chain == _CUSTOM_CHAIN and configured_style == "blockscout"
        if (self.chain in {"bsc", _CUSTOM_CHAIN} and not custom_blockscout) or "routescan" in base:
            request_params["chainid"] = chain_id
        if api_key:
            request_params["apikey"] = api_key
        try:
            response = httpx.get(base, params=request_params, timeout=20.0)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ConnectorUnavailable(f"Could not reach {base}: {exc}") from exc

        if not isinstance(data, dict):
            raise ConnectorUnavailable(f"Unexpected response from {base}: response was not a JSON object")
        message = str(data.get("message") or "").strip()
        result = data.get("result")
        normalized_message = message.lower()

        # Empty history is a successful sync. Explorers use status=0 and vary
        # the wording by endpoint, including "No token transfers found".
        if _is_empty_result(normalized_message, result):
            return [] if result is None or isinstance(result, str) else result

        status = str(data.get("status") or "")
        if status in {"1", "ok"} or normalized_message == "ok":
            return result

        # Etherscan returns the generic ``NOTOK`` label in ``message`` and
        # puts the useful diagnostic in ``result`` (for example ``Invalid API
        # Key`` or ``Max rate limit reached``). Preserve that detail so a
        # failed backfill tells the user what to fix instead of only saying
        # that the provider rejected the request.
        detail = str(result).strip() if isinstance(result, str) and result.strip() else ""
        if not detail or (detail.lower() == normalized_message and message):
            detail = message or str(result) or f"status {status or 'unknown'}"
        elif message and normalized_message not in {"notok", detail.lower()}:
            detail = f"{message}: {detail}"
        raise _ExplorerResponseError(base, action, detail)

    def _get(self, base: str, params: dict) -> list[dict]:
        result = self._get_raw(base, params)
        if result is None:
            return []
        if not isinstance(result, list):
            raise ConnectorUnavailable(f"Unexpected response from {base}: result was not a list")
        return [row for row in result if isinstance(row, dict)]

    PAGE_SIZE = 50
    BACKFILL_PAGES = 5

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

    def _paged_optional(self, base: str, action: str, pages: int) -> Iterable[dict]:
        try:
            yield from self._paged(base, action, pages)
        except _ExplorerResponseError as exc:
            if exc.action == action and action in _OPTIONAL_ACTIONS and _is_unsupported_message(exc.response_message):
                return
            raise

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        base, network, _chain_id, _api_key, native_symbol = self._network_config()
        if not _ETH_ADDRESS.fullmatch(self.address):
            raise ConnectorUnavailable(f"Invalid EVM address '{self.address}'. Use a 0x-prefixed 40-hex-character address.")
        if self._bsc_public_rpc_mode():
            # Only history for contracts the user explicitly named is
            # retrievable this way (see bsc_rpc) — native BNB transfers and
            # any undeclared token are permanently invisible to this path,
            # not a transient failure, so an account with no configured
            # contracts correctly imports zero events every sync.
            namespace = f"{self.source_id}:{self.chain}:{self.address}"
            for tx in bsc_rpc.fetch_token_transfers(self.address, self._bsc_token_contracts(), since):
                payload = {**tx, "_kind": "token", "_network": network, "_native_symbol": native_symbol}
                external_id = f"{tx['hash']}-token-{tx.get('contractAddress', '')}-{tx.get('logIndex', '0')}"
                yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)
            return
        namespace = f"{self.source_id}:{self.chain}:{self.address}"
        pages = self.BACKFILL_PAGES if since is None else 1

        for tx in self._paged(base, "txlist", pages):
            if tx.get("isError") == "1":
                continue
            has_value = tx.get("value", "0") != "0"
            is_contract_call = (tx.get("input") or "0x") != "0x"
            if not has_value and not is_contract_call:
                continue
            kind = "native" if has_value else "contract_call"
            payload = {**tx, "_kind": kind, "_network": network, "_native_symbol": native_symbol}
            yield RawRecord(namespace, tx["hash"], _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged_optional(base, "tokentx", pages):
            payload = {**tx, "_kind": "token", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-token-{tx.get('contractAddress', '')}-{tx.get('logIndex', '0')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged_optional(base, "tokennfttx", pages):
            payload = {**tx, "_kind": "nft721", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-nft721-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

        for tx in self._paged_optional(base, "token1155tx", pages):
            payload = {**tx, "_kind": "nft1155", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-nft1155-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield RawRecord(namespace, external_id, _timestamp(tx["timeStamp"]), payload)

    def fetch_balances(self) -> Iterable[Balance]:
        base, network, _chain_id, _api_key, native_symbol = self._network_config()
        if not _ETH_ADDRESS.fullmatch(self.address):
            raise ConnectorUnavailable(f"Invalid EVM address '{self.address}'. Use a 0x-prefixed 40-hex-character address.")
        if self._bsc_public_rpc_mode():
            return self._fetch_bsc_public_rpc_balances(network, native_symbol)
        balances: list[Balance] = []

        native_wei = self._get_raw(base, {"module": "account", "action": "balance", "address": self.address})
        try:
            native_amount = Decimal(str(native_wei)) / Decimal(10**18)
        except (InvalidOperation, TypeError, ValueError):
            native_amount = Decimal(0)
        if native_amount:
            balances.append(Balance(native_symbol, f"{native_amount:.18f}", asset_network=network, asset_type="COIN"))

        # tokenlist is not universally supported across Etherscan-compatible
        # explorers. A missing token list only means no token balances were
        # reported; the native balance above remains valid.
        try:
            tokens = self._get_raw(base, {"module": "account", "action": "tokenlist", "address": self.address}) or []
        except ConnectorUnavailable:
            tokens = []
        for token in tokens if isinstance(tokens, list) else []:
            if token.get("type") not in (None, "ERC-20"):
                continue
            try:
                decimals = int(token.get("decimals") or 18)
                raw_balance = int(token.get("balance", "0"))
            except (TypeError, ValueError):
                continue
            if raw_balance == 0:
                continue
            token_amount = Decimal(raw_balance) / (Decimal(10) ** decimals)
            balances.append(
                Balance(
                    token.get("symbol") or "UNKNOWN",
                    f"{token_amount:.{min(decimals, 18)}f}",
                    asset_network=network,
                    asset_contract=token.get("contractAddress"),
                    asset_type="TOKEN",
                )
            )
        return balances

    def _fetch_bsc_public_rpc_balances(self, network: str, native_symbol: str) -> list[Balance]:
        balances: list[Balance] = []
        native_amount = bsc_rpc.native_balance(self.address)
        if native_amount:
            balances.append(Balance(native_symbol, f"{native_amount:.18f}", asset_network=network, asset_type="COIN"))

        for contract in self._bsc_token_contracts():
            amount, decimals, symbol = bsc_rpc.token_balance(self.address, contract)
            if not amount:
                continue
            balances.append(
                Balance(
                    symbol or "UNKNOWN",
                    f"{amount:.{min(decimals, 18)}f}",
                    asset_network=network,
                    asset_contract=contract,
                    asset_type="TOKEN",
                )
            )
        return balances

    @property
    def history_limit_note(self) -> str | None:
        """Surfaced by sync_account() after a backfill (see Bitget/Binance
        for the same convention) — this is a real, permanent property of a
        BSC-without-a-key account, not a transient sync problem, so the
        user should know the shape of what is and isn't covered rather
        than assume the sync is broken."""
        if self._bsc_public_rpc_mode():
            return (
                "This BNB Smart Chain account is tracking a live native BNB balance plus the last 90 days of "
                "transfer history for USDT, USDC, BUSD, BTCB, ETH, and WBNB automatically, using free public BSC "
                "nodes — add more BEP-20 contract addresses in this source's settings to track other tokens the "
                "same way. Native BNB sends/receives can't be tracked this way (no free service indexes them), so "
                "those won't appear in Activity."
            )
        return None

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        kind = payload["_kind"]
        is_incoming = payload.get("to", "").lower() == self.address
        native_symbol = payload.get("_native_symbol") or _native_symbol(payload["_network"])

        if kind == "contract_call":
            gas_eth = int(payload["gasUsed"]) * int(payload["gasPrice"]) / 1e18
            return NormalizedEvent(
                event_type="UNKNOWN",
                event_subtype="contract_call",
                direction="-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=native_symbol,
                asset_network=payload["_network"],
                amount="0",
                source_label=self.account_label,
                counterparty=payload.get("to"),
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=f"Contract call, not decoded · {payload['hash'][:12]}…",
                fees=[NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=f"{gas_eth:.18f}")],
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
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=f"{gas_eth:.18f}"))
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="native",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=native_symbol,
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
        event_type = "NFT_MINT" if is_mint else "NFT_TRANSFER"
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
    return {"Polygon": "MATIC", "BNB Smart Chain": "BNB", "Avalanche": "AVAX"}.get(network, "ETH")


def _is_empty_result(message: str, result) -> bool:
    if any(marker in message for marker in _EMPTY_MARKERS):
        return result is None or result == [] or isinstance(result, str)
    return result == [] and message in {"", "ok"}


def _is_unsupported_message(message: str) -> bool:
    normalized = str(message or "").lower()
    return any(marker in normalized for marker in _UNSUPPORTED_MARKERS)
