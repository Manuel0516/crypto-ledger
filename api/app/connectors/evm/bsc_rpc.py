from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx

from app.connectors.base import ConnectorUnavailable

# Free, keyless public BSC RPC endpoints for basic state reads (balance,
# contract calls, block lookups) — tried in order until one answers.
# Etherscan removed BSC from its free API tier (a $49/mo "Lite" plan is now
# required), and no Blockscout instance indexes BSC mainnet (confirmed
# directly against Blockscout's own chain registry, which has no entry for
# chain id 56) — there is no free, keyless, address-indexed history API for
# BSC at all right now.
_PUBLIC_RPC_ENDPOINTS = (
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
)

# eth_getLogs is a separate story: BNB Chain's own official nodes (the
# _PUBLIC_RPC_ENDPOINTS above, and their bnbchain.org-branded twin) reject
# it outright — every attempt, at any window size down to a single block,
# with or without an address/topic filter, fails with a "limit exceeded"
# JSON-RPC error. Confirmed empirically. Two independently-operated public
# nodes do serve it, but only when scoped to one contract address (an
# unscoped, "every contract" scan gets refused there too, for the obvious
# reason that BSC's log volume is enormous) — which is exactly the shape
# this module needs, since a user names the specific BEP-20 contracts they
# want tracked. Public RPC providers enforce different result and block-range
# caps; all are handled by the adaptive window in _scan_logs_for_topic.
_LOG_RPC_ENDPOINTS = (
    "https://bsc-rpc.publicnode.com",
    "https://rpc-bsc.blockmachine.io",
)

_INITIAL_LOG_WINDOW_BLOCKS = 2000
_MIN_LOG_WINDOW_BLOCKS = 25
# A single flaky response from a public relay shouldn't cost an endpoint
# that's otherwise working. Retried in place before either
# shrinking the window or giving up on the endpoint entirely.
_MAX_TRANSIENT_RETRIES = 2
_TRANSIENT_RETRY_DELAY_SECONDS = 0.5
# Blocks to hold back from the chain tip before treating a transfer as
# final. BSC blocks are fast (~3s) but not instantly irreversible; a short
# reorg could otherwise make this app import a transfer that later
# vanishes from the canonical chain. Raw evidence is never corrected after
# insert, so this margin is the only defense available here.
_REORG_SAFETY_BLOCKS = 15
_AVG_BLOCK_SECONDS = 3
# No indexer means no way to bound "since genesis" to something tractable
# except a self-imposed lookback. A practical limit of this fallback path
# specifically, not a hard protocol limit. Kept well below a full year:
# every contract is scanned in both directions, so with several default
# contracts tracked automatically (_DEFAULT_BSC_TOKEN_CONTRACTS), a longer
# lookback would mean a first backfill spends hours making windowed
# eth_getLogs calls against rate-limited free infrastructure for tokens a
# given wallet may never have touched.
_BACKFILL_LOOKBACK_DAYS = 90

_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_DECIMALS_SELECTOR = "0x313ce567"  # decimals()
_SYMBOL_SELECTOR = "0x95d89b41"  # symbol()
_BALANCE_OF_SELECTOR = "0x70a08231"  # balanceOf(address)

# JSON-RPC error messages seen in practice for "this window is too big" —
# matched case-insensitively as a substring, since wording differs by
# provider (for example "query exceeds max results ...", "limit exceeded",
# or "log query range must not exceed N blocks"). Anything else gets a
# bounded same-window retry (see
# _MAX_TRANSIENT_RETRIES) before being treated as a real failure of that
# endpoint. "header not found" / "historical state is not available" can
# occur for a block range a different backend serves without complaint
# moments later.
_WINDOW_TOO_LARGE_MARKERS = ("limit exceeded", "exceeds max results", "block range", "blocks range", "too many", "must not exceed")


_HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (compatible; crypto-ledger/1.0)"}


def _call_endpoint(url: str, method: str, params: list) -> object:
    try:
        response = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=_HEADERS,
            timeout=20.0,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectorUnavailable(f"{url}: {exc}") from exc
    error = data.get("error") if isinstance(data, dict) else None
    if error:
        message = error.get("message", error) if isinstance(error, dict) else error
        raise ConnectorUnavailable(f"{url}: {message}")
    return data.get("result") if isinstance(data, dict) else None


def _rpc_call(method: str, params: list) -> object:
    last_error: Exception | None = None
    for url in _PUBLIC_RPC_ENDPOINTS:
        try:
            return _call_endpoint(url, method, params)
        except ConnectorUnavailable as exc:
            last_error = exc
            continue
    raise ConnectorUnavailable(f"No public BSC RPC endpoint answered: {last_error}")


def _pad_address(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def _hex_to_int(value: object) -> int:
    if not value or value in ("0x", "0X"):
        return 0
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return 0


def _decode_abi_string(hex_data: object) -> str | None:
    """Decode a Solidity ABI-encoded dynamic `string` return value (the
    standard shape for ERC-20 symbol()/name()). A token that instead
    returns a raw bytes32 (a small number of older contracts do) yields
    None here rather than a garbled decode — an unlabeled balance is
    honest; a wrong label isn't."""
    if not isinstance(hex_data, str) or not hex_data.startswith("0x") or len(hex_data) < 3:
        return None
    try:
        raw = bytes.fromhex(hex_data[2:])
    except ValueError:
        return None
    if len(raw) < 64:
        return None
    length = int.from_bytes(raw[32:64], "big")
    text = raw[64 : 64 + length]
    try:
        decoded = text.decode("utf-8").strip("\x00").strip()
    except UnicodeDecodeError:
        return None
    return decoded or None


def native_balance(address: str) -> Decimal:
    result = _rpc_call("eth_getBalance", [address, "latest"])
    try:
        return Decimal(_hex_to_int(result)) / Decimal(10**18)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _contract_metadata(contract: str) -> tuple[int, str | None]:
    """(decimals, symbol) for a BEP-20 contract. Never raises for a bad or
    non-standard contract: an unreadable decimals() or symbol() falls back
    to 18 decimals / no symbol rather than failing the whole lookup over
    one misbehaving token."""
    try:
        decimals = _hex_to_int(_rpc_call("eth_call", [{"to": contract, "data": _DECIMALS_SELECTOR}, "latest"])) or 18
    except ConnectorUnavailable:
        decimals = 18
    try:
        symbol = _decode_abi_string(_rpc_call("eth_call", [{"to": contract, "data": _SYMBOL_SELECTOR}, "latest"]))
    except ConnectorUnavailable:
        symbol = None
    return decimals, symbol


def token_balance(address: str, contract: str) -> tuple[Decimal, int, str | None]:
    """(amount, decimals, symbol) for one BEP-20 contract's balanceOf(address)."""
    call_data = "0x" + _BALANCE_OF_SELECTOR[2:] + _pad_address(address)
    raw_balance = _rpc_call("eth_call", [{"to": contract, "data": call_data}, "latest"])
    balance_units = _hex_to_int(raw_balance)
    decimals, symbol = _contract_metadata(contract)
    amount = Decimal(balance_units) / (Decimal(10) ** decimals)
    return amount, decimals, symbol


def _is_window_too_large(message: str) -> bool:
    normalized = message.lower()
    return any(marker in normalized for marker in _WINDOW_TOO_LARGE_MARKERS)


def _decode_transfer_log(log: dict) -> dict | None:
    topics = log.get("topics") or []
    if len(topics) < 3 or not isinstance(log.get("address"), str):
        return None
    from_addr = "0x" + str(topics[1])[-40:]
    to_addr = "0x" + str(topics[2])[-40:]
    return {
        "from": from_addr,
        "to": to_addr,
        "value": str(_hex_to_int(log.get("data"))),
        "contractAddress": log["address"],
        "hash": log.get("transactionHash"),
        "logIndex": str(_hex_to_int(log.get("logIndex"))),
        "blockNumber": str(_hex_to_int(log.get("blockNumber"))),
    }


def _scan_logs_for_topic(contract: str, wallet_topic: str, *, incoming: bool, from_block: int, to_block: int) -> Iterable[dict]:
    """Windowed eth_getLogs scan for one direction (incoming or outgoing)
    of Transfer events on one contract. Adapts to whichever window size
    the currently-used endpoint will actually serve: shrinks on a
    confirmed "too large" error and retries the same start point (never
    skips blocks); any other failure gets a few same-window retries first
    (see _MAX_TRANSIENT_RETRIES). Only once retries are exhausted at the
    floor window
    does it give up on the current endpoint and move to the next one in
    _LOG_RPC_ENDPOINTS — from the same start point, so a mid-scan endpoint
    failure never loses a block range, it just gets served by a different
    provider."""
    topics = [_TRANSFER_TOPIC, None, wallet_topic] if incoming else [_TRANSFER_TOPIC, wallet_topic]
    endpoint_index = 0
    window = _INITIAL_LOG_WINDOW_BLOCKS
    cursor = from_block
    transient_retries = 0
    while cursor <= to_block:
        window_end = min(cursor + window - 1, to_block)
        url = _LOG_RPC_ENDPOINTS[endpoint_index]
        try:
            result = _call_endpoint(
                url,
                "eth_getLogs",
                [{"fromBlock": hex(cursor), "toBlock": hex(window_end), "address": contract, "topics": topics}],
            )
        except ConnectorUnavailable as exc:
            if window > _MIN_LOG_WINDOW_BLOCKS and _is_window_too_large(str(exc)):
                window = max(_MIN_LOG_WINDOW_BLOCKS, window // 4)
                transient_retries = 0
                continue
            if transient_retries < _MAX_TRANSIENT_RETRIES:
                transient_retries += 1
                time.sleep(_TRANSIENT_RETRY_DELAY_SECONDS)
                continue
            transient_retries = 0
            endpoint_index += 1
            if endpoint_index >= len(_LOG_RPC_ENDPOINTS):
                raise ConnectorUnavailable(f"Could not scan BSC transfer logs for {contract}: {exc}") from exc
            window = _INITIAL_LOG_WINDOW_BLOCKS
            continue

        transient_retries = 0
        for log in result or []:
            if isinstance(log, dict) and not log.get("removed"):
                decoded = _decode_transfer_log(log)
                if decoded:
                    yield decoded
        cursor = window_end + 1
        # A successful call earns back a bigger window next time (capped
        # at the default) — otherwise one throttled window keeps every
        # later window artificially small for the rest of the scan.
        window = min(_INITIAL_LOG_WINDOW_BLOCKS, window * 2)


def _estimate_block_for_timestamp(target_ts: int, latest_block: int, latest_ts: int, *, safety_margin_seconds: int = 3600) -> int:
    """Approximate the block active at target_ts using BSC's ~3s average
    block time, biased earlier by a safety margin. Exact precision isn't
    required: raw evidence is deduplicated by (source_id, external_id), so
    re-scanning a bit of already-seen history is harmless, while an
    estimate that lands too late would silently create a real gap — the
    margin exists to make that failure mode the one this never picks."""
    elapsed = max(0, latest_ts - target_ts) + max(0, safety_margin_seconds)
    estimated_blocks_back = int(elapsed / _AVG_BLOCK_SECONDS)
    return max(0, latest_block - estimated_blocks_back)


def fetch_token_transfers(address: str, contracts: list[str], since) -> Iterable[dict]:
    """Real transfer history for specific, user-named BEP-20 contracts —
    the one case a free public BSC node will actually serve (see
    _LOG_RPC_ENDPOINTS). Yields dicts shaped exactly like an Etherscan
    `tokentx` row so EVMAddressConnector.normalize()'s existing "token"
    branch needs no changes to consume them."""
    if not contracts:
        return

    latest_block = _hex_to_int(_rpc_call("eth_blockNumber", []))
    to_block = max(0, latest_block - _REORG_SAFETY_BLOCKS)
    to_block_data = _rpc_call("eth_getBlockByNumber", [hex(to_block), False])
    latest_ts = _hex_to_int(to_block_data.get("timestamp")) if isinstance(to_block_data, dict) else 0

    if since is not None:
        from_block = _estimate_block_for_timestamp(int(since.timestamp()), to_block, latest_ts)
    else:
        lookback_seconds = _BACKFILL_LOOKBACK_DAYS * 24 * 3600
        from_block = _estimate_block_for_timestamp(latest_ts - lookback_seconds, to_block, latest_ts, safety_margin_seconds=0)
    if from_block > to_block:
        return

    wallet_topic = "0x" + _pad_address(address)
    block_timestamps: dict[int, int] = {}
    contract_meta: dict[str, tuple[int, str | None]] = {}

    # A failure scanning one contract (both public log endpoints down or
    # erroring for that specific range — real, if infrequent, given this
    # runs against free, unpaid infrastructure) must not cost every other
    # contract its data for this sync round too. Each (contract, direction)
    # pair degrades independently, same as _paged_optional does for the
    # Etherscan-key path's tokentx/tokennfttx/token1155tx endpoints. Only
    # after every pair has been attempted does a real failure get raised —
    # keeping whatever succeeded (sync_account() ingests everything already
    # yielded before an exception) while still reporting this round as
    # incomplete, so last_sync isn't advanced past a real gap.
    failures: list[str] = []
    for contract in contracts:
        contract = contract.lower()
        for incoming in (True, False):
            try:
                logs = list(_scan_logs_for_topic(contract, wallet_topic, incoming=incoming, from_block=from_block, to_block=to_block))
            except ConnectorUnavailable as exc:
                failures.append(f"{contract} ({'incoming' if incoming else 'outgoing'}): {exc}")
                continue
            for log in logs:
                block_number = int(log["blockNumber"])
                if block_number not in block_timestamps:
                    block_data = _rpc_call("eth_getBlockByNumber", [hex(block_number), False])
                    block_timestamps[block_number] = _hex_to_int(block_data.get("timestamp")) if isinstance(block_data, dict) else 0
                if contract not in contract_meta:
                    contract_meta[contract] = _contract_metadata(contract)
                decimals, symbol = contract_meta[contract]
                yield {
                    **log,
                    "tokenDecimal": str(decimals),
                    "tokenSymbol": symbol or "UNKNOWN",
                    "tokenName": symbol or "Unknown token",
                    "timeStamp": str(block_timestamps[block_number]),
                }

    if failures:
        raise ConnectorUnavailable(f"Could not scan every configured contract this round: {'; '.join(failures)}")
