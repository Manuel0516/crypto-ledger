from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord
from app.connectors.evm import bsc_rpc, bsctrace

# An EVM address is not enough to identify a chain: the same 0x address can
# exist on every EVM network. Keep built-in networks explicit and never map an
# unknown value back to Ethereum.
#
# Blockscout exposes a public Etherscan-compatible API for the first group.
# BSC uses MegaNode/BSCTrace when a bsc_trace_api_key is configured, with the
# old Etherscan-compatible path retained for existing accounts. Custom EVM
# networks use the same Etherscan-compatible shape by default and can
# override the endpoint in the encrypted account config.
CHAINS: dict[str, tuple[str, str]] = {
    "ethereum": ("https://eth.blockscout.com/api", "Ethereum"),
    "polygon": ("https://polygon.blockscout.com/api", "Polygon"),
    "arbitrum": ("https://arbitrum.blockscout.com/api", "Arbitrum"),
    "optimism": ("https://optimism.blockscout.com/api", "Optimism"),
    "base": ("https://base.blockscout.com/api", "Base"),
    "bsc": ("https://api.etherscan.io/v2/api", "BNB Smart Chain"),
    "avalanche": ("https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api", "Avalanche"),
}

# Contract addresses of known liquidity-position managers, keyed by lowercase
# address. A wallet receiving a fresh position NFT from one of these is a
# liquidity deposit, not a collectible — without this, the generic NFT path
# below would file it as NFT_MINT. Verified against real PancakeSwap V3
# activity — the same CREATE2-deployed address across BSC, Ethereum,
# Arbitrum, and Base. Extend this dict as other protocols are confirmed
# rather than guessing at addresses that haven't been observed on-chain.
_LP_POSITION_MANAGERS: dict[str, str] = {
    "0x46a15b0b27311cedf172ab29e4f4766fbe7f4364": "PancakeSwap V3",
}

# Known wrapped-native token contracts, keyed by lowercase address. Calling
# withdraw() on one of these doesn't reliably emit an indexed ERC-20 Transfer
# log the same way an ordinary token move does — the explorer only ever
# reports a zero-value call to the contract plus a plain native transfer back
# — so there is no token leg for the swap merge below to pair against.
# Unwrapping (and wrapping) is a one-for-one swap between the token and its
# native coin; recognizing the contract lets that leg be filled in exactly
# rather than guessed at.
_WRAPPED_NATIVE_CONTRACTS: dict[str, str] = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": "WBNB",
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
# key (see EVMAddressConnector._bsc_token_contracts). Keep this deliberately
# small for free public RPC providers: USDC and WBNB cover the keyless default
# use case, while native BNB is tracked separately as a live balance because
# it is not a BEP-20 contract. Each address was confirmed on-chain
# (symbol()/decimals() read back as expected) before being hardcoded here.
_DEFAULT_BSC_TOKEN_CONTRACTS: tuple[str, ...] = (
    "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",  # USDC
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
        return "evm-address-0.7"

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
        """True when this account has neither an indexed nor legacy key."""
        _base, _network, _chain_id, api_key, _native = self._network_config()
        return self.chain == "bsc" and not api_key and not self._bsc_trace_api_key()

    def _bsc_trace_api_key(self) -> str | None:
        key = str(self.config.get("bsc_trace_api_key") or "").strip()
        return key or None

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
        namespace = f"{self.source_id}:{self.chain}:{self.address}"
        raw_entries = self._fetch_raw_payloads(base, network, native_symbol, since)
        wrap_aware_entries = _synthesize_wrap_legs(raw_entries, self.address)
        merged_entries = _merge_swap_legs(_merge_lp_deposits(wrap_aware_entries, self.address), self.address)
        for payload, external_id in _relabel_lp_collects(merged_entries, self.address):
            yield RawRecord(namespace, external_id, _timestamp(payload["timeStamp"]), payload)

    def _fetch_raw_payloads(self, base: str, network: str, native_symbol: str, since: datetime | None) -> Iterable[tuple[dict, str]]:
        """Yield (payload, external_id) pairs exactly as before swap merging."""
        trace_key = self._bsc_trace_api_key()
        if self.chain == "bsc" and trace_key:
            for tx in bsctrace.fetch_transfers(self.address, trace_key, since):
                payload = {**tx, "_network": network, "_native_symbol": native_symbol}
                if tx["_kind"] == "contract_call":
                    external_id = tx["hash"]
                elif tx["_kind"] == "nft1155":
                    external_id = f"{tx['hash']}-nft1155-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}-{tx.get('_item_index', '0')}"
                else:
                    external_id = f"{tx['hash']}-{tx['_kind']}-{tx.get('contractAddress', '')}-{tx.get('tokenID', tx.get('logIndex', '0'))}"
                yield payload, external_id
            return
        if self._bsc_public_rpc_mode():
            # Only history for contracts the user explicitly named is
            # retrievable this way (see bsc_rpc) — native BNB transfers and
            # any undeclared token are permanently invisible to this path,
            # not a transient failure, so an account with no configured
            # contracts correctly imports zero events every sync.
            for tx in bsc_rpc.fetch_token_transfers(self.address, self._bsc_token_contracts(), since):
                payload = {**tx, "_kind": "token", "_network": network, "_native_symbol": native_symbol}
                external_id = f"{tx['hash']}-token-{tx.get('contractAddress', '')}-{tx.get('logIndex', '0')}"
                yield payload, external_id
            return
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
            yield payload, tx["hash"]

        for tx in self._paged_optional(base, "tokentx", pages):
            payload = {**tx, "_kind": "token", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-token-{tx.get('contractAddress', '')}-{tx.get('logIndex', '0')}"
            yield payload, external_id

        for tx in self._paged_optional(base, "tokennfttx", pages):
            payload = {**tx, "_kind": "nft721", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-nft721-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield payload, external_id

        for tx in self._paged_optional(base, "token1155tx", pages):
            payload = {**tx, "_kind": "nft1155", "_network": network, "_native_symbol": native_symbol}
            external_id = f"{tx['hash']}-nft1155-{tx.get('contractAddress', '')}-{tx.get('tokenID', '')}"
            yield payload, external_id

    def fetch_balances(self) -> Iterable[Balance]:
        base, network, _chain_id, _api_key, native_symbol = self._network_config()
        if not _ETH_ADDRESS.fullmatch(self.address):
            raise ConnectorUnavailable(f"Invalid EVM address '{self.address}'. Use a 0x-prefixed 40-hex-character address.")
        trace_key = self._bsc_trace_api_key()
        if self.chain == "bsc" and trace_key:
            return bsctrace.fetch_balances(self.address, trace_key)
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
        """Explain the deliberately limited no-key fallback to the user."""
        if self._bsc_public_rpc_mode():
            return (
                "This BNB Smart Chain account is tracking a live native BNB balance plus the last 90 days of "
                "transfer history for USDC and WBNB automatically, using free public BSC "
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
                account_name=self.account_label,
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
            lp_collect = payload.get("_lp_collect_protocol")
            event_type, direction = ("DEPOSIT", "+") if is_incoming else ("WITHDRAWAL", "-")
            amount = int(payload["value"]) / 1e18
            fees: list[NormalizedFee] = []
            if not is_incoming and not payload.get("_internal"):
                gas_eth = int(payload["gasUsed"]) * int(payload["gasPrice"]) / 1e18
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=f"{gas_eth:.18f}"))
            if lp_collect and is_incoming:
                event_type = "LIQUIDITY"
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="dex_lp_collect" if lp_collect and is_incoming else "native",
                direction=direction,
                status="REQUIRES_REVIEW" if lp_collect and is_incoming else "COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=native_symbol,
                asset_network=payload["_network"],
                amount=f"{amount:.18f}",
                account_name=self.account_label,
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=(
                    f"{lp_collect} liquidity collected · confirm whether this is fee income or a withdrawal of "
                    f"principal · {payload['hash'][:12]}…"
                    if lp_collect and is_incoming
                    else f"On-chain tx {payload['hash'][:12]}…"
                ),
                fees=fees,
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
            )

        if kind == "token":
            lp_collect = payload.get("_lp_collect_protocol")
            event_type, direction = ("DEPOSIT", "+") if is_incoming else ("WITHDRAWAL", "-")
            decimals = int(payload.get("tokenDecimal") or 18)
            amount = int(payload["value"]) / (10**decimals)
            fees: list[NormalizedFee] = []
            if payload.get("_fee_for_wallet"):
                gas_eth = int(payload.get("gasUsed") or 0) * int(payload.get("gasPrice") or 0) / 1e18
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=f"{gas_eth:.18f}"))
            if lp_collect and is_incoming:
                event_type = "LIQUIDITY"
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="dex_lp_collect" if lp_collect and is_incoming else "token",
                direction=direction,
                status="REQUIRES_REVIEW" if lp_collect and is_incoming else "COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=payload.get("tokenSymbol") or "UNKNOWN",
                asset_network=payload["_network"],
                asset_contract=payload.get("contractAddress"),
                asset_type="TOKEN",
                amount=f"{amount:.{min(decimals, 18)}f}",
                account_name=self.account_label,
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=(
                    f"{lp_collect} liquidity collected · confirm whether this is fee income or a withdrawal of "
                    f"principal · {payload['hash'][:12]}…"
                    if lp_collect and is_incoming
                    else f"{payload.get('tokenName') or 'Token'} transfer {payload['hash'][:12]}…"
                ),
                fees=fees,
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
                log_index=_maybe_int(payload.get("logIndex")),
                contract_address=payload.get("contractAddress"),
            )

        if kind == "swap":
            fees: list[NormalizedFee] = []
            if payload.get("_gas_eth"):
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=payload["_gas_eth"]))
            return NormalizedEvent(
                event_type="SWAP",
                event_subtype="dex_swap",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=payload["_out_symbol"],
                asset_network=payload["_network"],
                asset_contract=payload.get("_out_contract"),
                asset_type=payload["_out_asset_type"],
                amount=payload["_out_amount"],
                account_name=self.account_label,
                address_from=payload.get("from"),
                address_to=payload.get("to"),
                notes=f"On-chain swap {payload['hash'][:12]}…",
                fees=fees,
                secondary_asset_symbol=payload["_in_symbol"],
                secondary_asset_network=payload["_network"],
                secondary_amount=payload["_in_amount"],
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
                contract_address=payload.get("to"),
            )

        if kind == "lp_deposit":
            fees: list[NormalizedFee] = []
            if payload.get("_gas_eth"):
                fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=payload["_gas_eth"]))
            token_id = payload.get("_position_token_id") or "?"
            return NormalizedEvent(
                event_type="LIQUIDITY",
                event_subtype="dex_lp_deposit",
                direction="-",
                # The facts (asset, amount, tx) are confident — this isn't a
                # data-quality flag. Tax treatment for adding liquidity is
                # never assigned automatically (plan: LIQUIDITY needs a
                # person's decision), so it's a real thing to review, and
                # "Mark reviewed" (see api/events.py) is how it stops
                # reappearing once a person has actually looked at it.
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=payload["_out_symbol"],
                asset_network=payload["_network"],
                asset_contract=payload.get("_out_contract"),
                asset_type=payload["_out_asset_type"],
                amount=payload["_out_amount"],
                account_name=self.account_label,
                address_from=payload.get("from"),
                address_to=payload.get("_position_contract"),
                notes=f"{payload['_protocol']} liquidity position #{token_id} · {payload['hash'][:12]}…",
                fees=fees,
                secondary_asset_symbol=payload.get("_in_symbol"),
                secondary_asset_network=payload["_network"] if payload.get("_in_symbol") else None,
                secondary_amount=payload.get("_in_amount"),
                tx_hash=payload.get("hash"),
                block_height=_maybe_int(payload.get("blockNumber")),
                block_hash=payload.get("blockHash"),
                contract_address=payload.get("_position_contract"),
            )

        # nft721 / nft1155 — a mint is treated as income (received for free,
        # same as RP2's own vocabulary); an ordinary transfer folds into
        # DEPOSIT/WITHDRAWAL by direction rather than TRANSFER, which would
        # incorrectly auto-flag it as an internal move (see service.py's
        # ingest()).
        is_mint = payload.get("from", "").lower() == _ZERO_ADDRESS
        event_type = "INCOME" if is_mint else ("DEPOSIT" if is_incoming else "WITHDRAWAL")
        direction = "+" if is_incoming else "-"
        quantity = payload.get("tokenValue", "1") if kind == "nft1155" else "1"
        token_id = payload.get("tokenID", "")
        standard = "ERC-1155" if kind == "nft1155" else "ERC-721"
        fees: list[NormalizedFee] = []
        if payload.get("_fee_for_wallet"):
            gas_eth = int(payload.get("gasUsed") or 0) * int(payload.get("gasPrice") or 0) / 1e18
            fees.append(NormalizedFee(fee_type="GAS_FEE", asset_symbol=native_symbol, amount=f"{gas_eth:.18f}"))
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
            account_name=self.account_label,
            address_from=payload.get("from"),
            address_to=payload.get("to"),
            notes=f"{standard} {payload.get('tokenName') or 'NFT'} #{token_id} · {payload['hash'][:12]}…",
            fees=fees,
            tx_hash=payload.get("hash"),
            block_height=_maybe_int(payload.get("blockNumber")),
            block_hash=payload.get("blockHash"),
            log_index=_maybe_int(payload.get("logIndex")),
            contract_address=payload.get("contractAddress"),
        )


def _leg_identity(payload: dict) -> tuple[str, str]:
    if payload["_kind"] == "token":
        return str(payload.get("tokenSymbol") or "UNKNOWN").upper(), str(payload.get("contractAddress") or "").lower()
    return str(payload.get("_native_symbol") or "").upper(), ""


def _leg_measure(payload: dict) -> tuple[str, str, str | None, str]:
    """(symbol, amount, contract, asset_type) for a native or token leg."""
    if payload["_kind"] == "token":
        decimals = int(payload.get("tokenDecimal") or 18)
        amount = int(payload["value"]) / (10**decimals)
        return payload.get("tokenSymbol") or "UNKNOWN", f"{amount:.{min(decimals, 18)}f}", payload.get("contractAddress"), "TOKEN"
    amount = int(payload["value"]) / 1e18
    return payload.get("_native_symbol") or "", f"{amount:.18f}", None, "COIN"


def _leg_gas_fee(leg: dict, group: list[tuple[dict, str]]) -> float | None:
    """ETH-denominated gas cost to attribute to a merged event, mirroring the
    same one-fee-per-wallet-leg convention used everywhere else in this file
    (see bsctrace.py's ``_fee_for_wallet``) so a merged event never double- or
    zero-counts the transaction's gas."""
    if leg["_kind"] == "native" and not leg.get("_internal"):
        return int(leg["gasUsed"]) * int(leg["gasPrice"]) / 1e18
    if leg.get("_fee_for_wallet"):
        return int(leg.get("gasUsed") or 0) * int(leg.get("gasPrice") or 0) / 1e18
    gas_leg = next((p for p, _ in group if p["_kind"] == "contract_call"), None)
    if gas_leg is not None:
        return int(gas_leg["gasUsed"]) * int(gas_leg["gasPrice"]) / 1e18
    return None


def _synthesize_wrap_legs(entries: Iterable[tuple[dict, str]], address: str) -> Iterable[tuple[dict, str]]:
    """Fill in the missing wrapped-token leg of a wrap/unwrap call so the
    ordinary swap merge below can recognize it as one swap instead of a bare
    zero-value contract call plus an unrelated-looking native transfer.

    Only synthesizes a leg when the transaction has a zero-value call from
    the wallet to a known wrapped-native contract, a paired native transfer
    to/from that same contract, and no token leg already accounts for it —
    the synthetic leg's amount is exactly the real native leg's amount
    (wrapped tokens are one-for-one with their native coin), never guessed.
    """
    address = address.lower()
    buffered = list(entries)
    by_hash: dict[str, list[tuple[dict, str]]] = {}
    for payload, external_id in buffered:
        by_hash.setdefault(payload["hash"], []).append((payload, external_id))

    extra: list[tuple[dict, str]] = []
    replaced_native_ids: set[str] = set()
    for tx_hash, group in by_hash.items():
        call = next(
            (
                p
                for p, _ in group
                if p["_kind"] == "contract_call"
                and p.get("from", "").lower() == address
                and p.get("to", "").lower() in _WRAPPED_NATIVE_CONTRACTS
            ),
            None,
        )
        if call is None:
            continue
        contract = call["to"].lower()
        already_has_leg = any(p["_kind"] == "token" and p.get("contractAddress", "").lower() == contract for p, _ in group)
        if already_has_leg:
            continue
        native_entry = next(
            (
                (p, eid)
                for p, eid in group
                if p["_kind"] == "native"
                and (
                    (p.get("from", "").lower() == contract and p.get("to", "").lower() == address)
                    or (p.get("from", "").lower() == address and p.get("to", "").lower() == contract)
                )
            ),
            None,
        )
        if native_entry is None:
            continue
        native, native_external_id = native_entry
        unwrapping = native.get("from", "").lower() == contract
        symbol = _WRAPPED_NATIVE_CONTRACTS[contract]
        synthetic = {
            **native,
            "_kind": "token",
            "_internal": False,
            "from": address if unwrapping else contract,
            "to": contract if unwrapping else address,
            "contractAddress": call["to"],
            "tokenSymbol": symbol,
            "tokenName": symbol,
            "tokenDecimal": "18",
            "logIndex": f"wrap-{native.get('logIndex', '0')}",
        }
        extra.append((synthetic, f"{tx_hash}-wrap-{contract}"))
        # The counterparty is now a known, recognized wrapped-native
        # contract rather than an unattributed internal transfer — eligible
        # for the swap merge below the same as any ordinary paired leg.
        extra.append(({**native, "_internal": False}, native_external_id))
        replaced_native_ids.add(native_external_id)

    for payload, external_id in buffered:
        if external_id in replaced_native_ids:
            continue
        extra.append((payload, external_id))
    yield from extra


def _relabel_lp_collects(entries: Iterable[tuple[dict, str]], address: str) -> Iterable[tuple[dict, str]]:
    """Flag a token/native leg arriving from a known LP position manager, in
    a transaction with no NFT leg, as liquidity collected from an existing
    position rather than a plain deposit — normalize() turns this into a
    LIQUIDITY event needing review instead of a generic DEPOSIT.

    Whether a given collect() call is fee income or a partial/full return of
    principal isn't observable from the transfer legs alone — the two look
    identical from outside the contract — so this never picks one; it only
    makes sure the activity is never mislabeled as if it came from nowhere.
    A mint (deposit, an NFT arrives) or a full withdrawal (the position NFT
    is burned) has its own NFT leg and is handled elsewhere, so a group with
    an NFT leg is left untouched here.
    """
    address = address.lower()
    buffered = list(entries)
    by_hash: dict[str, list[tuple[dict, str]]] = {}
    for payload, external_id in buffered:
        by_hash.setdefault(payload["hash"], []).append((payload, external_id))

    collect_hashes: dict[str, str] = {}
    for tx_hash, group in by_hash.items():
        call = next(
            (
                p
                for p, _ in group
                if p["_kind"] == "contract_call"
                and p.get("from", "").lower() == address
                and p.get("to", "").lower() in _LP_POSITION_MANAGERS
            ),
            None,
        )
        if call is None or any(p["_kind"] in ("nft721", "nft1155") for p, _ in group):
            continue
        collect_hashes[tx_hash] = _LP_POSITION_MANAGERS[call["to"].lower()]

    for payload, external_id in buffered:
        protocol = collect_hashes.get(payload["hash"])
        # Only the leg arriving at the wallet is the collected amount; the
        # wallet's own outgoing leg in the same tx (if any) is left as-is.
        if protocol and payload["_kind"] in ("native", "token") and payload.get("to", "").lower() == address:
            payload = {**payload, "_lp_collect_protocol": protocol}
        yield payload, external_id


def _merge_lp_deposits(entries: Iterable[tuple[dict, str]], address: str) -> Iterable[tuple[dict, str]]:
    """Recognize minting a liquidity position (plan §15's liquidity case) as
    one LIQUIDITY event instead of a bare NFT arrival plus the token legs that
    funded it. A position-manager mint transaction is a fresh position NFT
    (from the zero address) at a known ``_LP_POSITION_MANAGERS`` contract,
    alongside one or two native/token legs the wallet itself sent in the same
    transaction — those are the deposited amounts. Anything else (an unknown
    contract, zero or more than two funding legs) is left as individual
    transfers: this only recognizes the unambiguous case, never guesses.
    """
    address = address.lower()
    buffered = list(entries)
    by_hash: dict[str, list[tuple[dict, str]]] = {}
    for payload, external_id in buffered:
        by_hash.setdefault(payload["hash"], []).append((payload, external_id))

    merged_hashes: set[str] = set()
    for tx_hash, group in by_hash.items():
        mint = next(
            (
                p
                for p, _ in group
                if p["_kind"] in ("nft721", "nft1155")
                and p.get("from", "").lower() == _ZERO_ADDRESS
                and p.get("contractAddress", "").lower() in _LP_POSITION_MANAGERS
            ),
            None,
        )
        if mint is None:
            continue
        funding_legs = [p for p, _ in group if p["_kind"] in ("native", "token") and p.get("from", "").lower() == address]
        if not 1 <= len(funding_legs) <= 2:
            continue

        primary, secondary = funding_legs[0], funding_legs[1] if len(funding_legs) == 2 else None
        gas_eth = _leg_gas_fee(primary, group) or (secondary and _leg_gas_fee(secondary, group))
        out_symbol, out_amount, out_contract, out_type = _leg_measure(primary)
        lp_payload = {
            **primary,
            "_kind": "lp_deposit",
            "_protocol": _LP_POSITION_MANAGERS[mint["contractAddress"].lower()],
            "_position_token_id": mint.get("tokenID", ""),
            "_position_contract": mint.get("contractAddress"),
            "_out_symbol": out_symbol,
            "_out_amount": out_amount,
            "_out_contract": out_contract,
            "_out_asset_type": out_type,
            "_gas_eth": f"{gas_eth:.18f}" if gas_eth else None,
        }
        if secondary is not None:
            in_symbol, in_amount, _c, _t = _leg_measure(secondary)
            lp_payload["_in_symbol"] = in_symbol
            lp_payload["_in_amount"] = in_amount
        merged_hashes.add(tx_hash)
        yield lp_payload, f"{tx_hash}-lp-deposit"

    for payload, external_id in buffered:
        if payload["hash"] in merged_hashes and (
            payload["_kind"] in ("native", "token", "contract_call")
            or (payload["_kind"] in ("nft721", "nft1155") and payload.get("contractAddress", "").lower() in _LP_POSITION_MANAGERS)
        ):
            continue
        yield payload, external_id


def _merge_swap_legs(entries: Iterable[tuple[dict, str]], address: str) -> Iterable[tuple[dict, str]]:
    """Fold an exact two-leg, opposite-direction, different-asset transaction
    into one swap payload, mirroring how a swap is already a single event
    with two legs everywhere else in this app (plan §15: "BTC -0.001 / ETH
    +0.034"). A DEX swap on a single tracked address otherwise arrives as two
    independent transfers (the sent leg, the received leg) that would each
    need manual merging into a swap after the fact. Only the unambiguous
    two-leg case is recognized here — a multi-hop route or a staking/
    liquidity interaction has a different leg count and is left as
    individual transfers rather than guessed at.
    """
    address = address.lower()
    buffered = list(entries)
    by_hash: dict[str, list[tuple[dict, str]]] = {}
    for payload, external_id in buffered:
        by_hash.setdefault(payload["hash"], []).append((payload, external_id))

    merged_hashes: set[str] = set()
    for tx_hash, group in by_hash.items():
        legs = [p for p, _ in group if p["_kind"] in ("native", "token") and not p.get("_internal")]
        if len(legs) != 2:
            continue
        first, second = legs
        first_in = first.get("to", "").lower() == address
        second_in = second.get("to", "").lower() == address
        if first_in == second_in or _leg_identity(first) == _leg_identity(second):
            continue
        outgoing, incoming = (second, first) if first_in else (first, second)

        gas_eth = _leg_gas_fee(outgoing, group)
        out_symbol, out_amount, out_contract, out_type = _leg_measure(outgoing)
        in_symbol, in_amount, _in_contract, _in_type = _leg_measure(incoming)
        swap_payload = {
            **outgoing,
            "_kind": "swap",
            "_out_symbol": out_symbol,
            "_out_amount": out_amount,
            "_out_contract": out_contract,
            "_out_asset_type": out_type,
            "_in_symbol": in_symbol,
            "_in_amount": in_amount,
            "_gas_eth": f"{gas_eth:.18f}" if gas_eth is not None else None,
        }
        merged_hashes.add(tx_hash)
        yield swap_payload, f"{tx_hash}-swap"

    for payload, external_id in buffered:
        if payload["hash"] in merged_hashes and payload["_kind"] in ("native", "token", "contract_call"):
            continue
        yield payload, external_id


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
