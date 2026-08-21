from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable


BASE_URL = "https://bsc-mainnet.nodereal.io/v1"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "crypto-ledger/1.0"}
_PAGE_SIZE = 1000
_MAX_PAGES = 1000
_BLOCK_SECONDS = 3
_TRANSFER_CATEGORIES = ["external", "internal", "20", "721", "1155"]


def _endpoint(api_key: str) -> str:
    key = str(api_key or "").strip()
    if not key:
        raise ConnectorUnavailable("BSCTrace/MegaNode API key is missing.")
    return f"{BASE_URL}/{key}"


def _call(api_key: str, method: str, params: list | tuple) -> object:
    try:
        response = httpx.post(
            _endpoint(api_key),
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)},
            headers=_HEADERS,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        # Do not include the endpoint in the error: the API key is part of
        # that URL and must never leak into the UI or application logs.
        raise ConnectorUnavailable(
            f"BSCTrace/MegaNode {method} request failed with HTTP {exc.response.status_code}."
        ) from exc
    except httpx.HTTPError as exc:
        raise ConnectorUnavailable(f"BSCTrace/MegaNode {method} request failed: {type(exc).__name__}.") from exc
    except ValueError as exc:
        raise ConnectorUnavailable(f"BSCTrace/MegaNode {method} returned invalid JSON: {exc}.") from exc

    if not isinstance(data, dict):
        raise ConnectorUnavailable(f"BSCTrace/MegaNode {method} returned a non-JSON response.")
    error = data.get("error")
    if error:
        detail = error.get("message", error) if isinstance(error, dict) else error
        raise ConnectorUnavailable(f"BSCTrace/MegaNode {method} failed: {detail}")
    if "result" not in data:
        raise ConnectorUnavailable(f"BSCTrace/MegaNode {method} returned no result.")
    return data["result"]


def _intish(value: object, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        text = str(value)
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return default


def _timestamp(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _estimated_from_block(api_key: str, since: datetime) -> int | None:
    """Convert a recent sync timestamp to a bounded indexed block range.

    MegaNode limits nr_getAssetTransfers block ranges to 100,000 blocks. For
    an old or unusually delayed sync, returning None lets the indexed API
    paginate from the account history instead of sending an invalid range.
    Results are still filtered by their actual block timestamp below.
    """
    latest = _intish(_call(api_key, "eth_blockNumber", []), -1)
    if latest < 0:
        return None
    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    age = max(0, (datetime.now(timezone.utc) - since_utc).total_seconds()) + 900
    from_block = max(0, latest - int(age / _BLOCK_SECONDS))
    return from_block if latest - from_block <= 100_000 else None


def _transfer_payloads(row: dict) -> list[dict]:
    category = str(row.get("category") or "").lower()
    timestamp = _timestamp(row.get("blockTimestamp", row.get("blockTimeStamp")))
    tx_hash = str(row.get("hash") or "")
    from_address = str(row.get("from") or "")
    to_address = str(row.get("to") or "")
    if not tx_hash or timestamp is None or not from_address or not to_address:
        return []
    if str(row.get("receiptsStatus") or "1") in {"0", "0x0"}:
        return []

    common = {
        "hash": tx_hash,
        "from": from_address,
        "to": to_address,
        "value": str(_intish(row.get("value"))),
        "timeStamp": str(timestamp),
        "blockNumber": str(_intish(row.get("blockNum"))),
        "gasUsed": str(_intish(row.get("gasUsed"))),
        "gasPrice": str(_intish(row.get("gasPrice"))),
    }
    if category == "20":
        return [
            {
                **common,
                "_kind": "token",
                "contractAddress": row.get("contractAddress"),
                "tokenDecimal": str(_intish(row.get("decimal"), 18)),
                "tokenSymbol": row.get("asset") or "UNKNOWN",
                "tokenName": row.get("asset") or "Token",
                "logIndex": str(_intish(row.get("logIndex"))),
            }
        ]
    if category == "721":
        return [
            {
                **common,
                "_kind": "nft721",
                "contractAddress": row.get("contractAddress"),
                "tokenID": str(_intish(row.get("erc721TokenId"))),
                "tokenSymbol": row.get("asset") or "NFT",
                "tokenName": row.get("asset") or "NFT",
                "logIndex": str(_intish(row.get("logIndex"))),
            }
        ]
    if category == "1155":
        metadata = row.get("erc1155Metadata") or row.get("erc1155MetaData") or []
        if not isinstance(metadata, list):
            metadata = []
        return [
            {
                **common,
                "_kind": "nft1155",
                "contractAddress": row.get("contractAddress"),
                "tokenID": str(_intish(item.get("tokenId"))) if isinstance(item, dict) else "0",
                "tokenValue": str(_intish(item.get("value"))) if isinstance(item, dict) else "0",
                "tokenSymbol": row.get("asset") or "NFT",
                "tokenName": row.get("asset") or "NFT",
                "logIndex": str(_intish(row.get("logIndex"))),
                "_item_index": index,
            }
            for index, item in enumerate(metadata)
        ]
    if category in {"external", "internal"}:
        # MegaNode's external category includes contract calls that emit no
        # asset transfer. Preserve those as the same reviewable UNKNOWN event
        # the Etherscan connector produces; zero-value internal rows carry no
        # useful ledger movement and can be ignored.
        if category == "internal" and _intish(row.get("value")) == 0:
            return []
        kind = "contract_call" if category == "external" and _intish(row.get("value")) == 0 else "native"
        return [{**common, "_kind": kind, "_internal": category == "internal"}]
    return []


def _enrich_payload(api_key: str, payload: dict, cache: dict[str, dict | None]) -> dict:
    """Attach transaction-level evidence to one asset-transfer payload.

    nr_getAssetTransfers is optimized for finding asset legs and does not
    include the block hash or complete receipt/transaction metadata. The
    detail endpoint supplies those fields. Enrichment is best effort: an
    indexed transfer is still valid evidence if a detail lookup is throttled
    or temporarily unavailable.
    """
    tx_hash = str(payload.get("hash") or "")
    if not tx_hash:
        return payload
    if tx_hash not in cache:
        try:
            detail = _call(api_key, "nr_getTransactionDetail", [tx_hash])
            cache[tx_hash] = detail if isinstance(detail, dict) else None
        except ConnectorUnavailable:
            cache[tx_hash] = None
    detail = cache[tx_hash]
    if not detail:
        return payload

    payload["_transaction_detail"] = detail
    if detail.get("blockHash"):
        payload["blockHash"] = detail["blockHash"]
    if detail.get("blockNumber") is not None:
        payload["blockNumber"] = str(_intish(detail["blockNumber"]))
    if detail.get("blockTimeStamp") is not None:
        payload["timeStamp"] = str(_timestamp(detail["blockTimeStamp"]) or payload["timeStamp"])
    ethereum = detail.get("ethereumSpecific")
    if isinstance(ethereum, dict):
        if ethereum.get("gasUsed") is not None:
            payload["gasUsed"] = str(_intish(ethereum["gasUsed"]))
        if ethereum.get("gasPrice") is not None:
            payload["gasPrice"] = str(_intish(ethereum["gasPrice"]))
        if ethereum.get("input"):
            payload["input"] = ethereum["input"]
        if ethereum.get("nonce") is not None:
            payload["nonce"] = _intish(ethereum["nonce"])
        if ethereum.get("transactionIndex") is not None:
            payload["transactionIndex"] = _intish(ethereum["transactionIndex"])
    if detail.get("fees") is not None:
        payload["_transaction_fee_wei"] = str(_intish(detail["fees"]))
    return payload


def fetch_transfers(address: str, api_key: str, since: datetime | None = None) -> Iterable[dict]:
    """Yield Etherscan-shaped payloads from MegaNode's indexed BSC history."""
    from_block = _estimated_from_block(api_key, since) if since else None
    seen: set[tuple[str, str, str, str]] = set()
    transaction_cache: dict[str, dict | None] = {}
    fee_attached_hashes: set[str] = set()

    # Query both sides explicitly. MegaNode's enhanced API supports these
    # filters and this avoids relying on an undocumented "all addresses"
    # interpretation of nr_getAssetTransfers.
    for address_key in ("fromAddress", "toAddress"):
        page_key: str | None = None
        for _page in range(_MAX_PAGES):
            params: dict[str, object] = {
                address_key: address,
                "category": _TRANSFER_CATEGORIES,
                "order": "asc",
                "excludeZeroValue": False,
                "maxCount": hex(_PAGE_SIZE),
            }
            if from_block is not None:
                params["fromBlock"] = hex(from_block)
                params["toBlock"] = "latest"
            if page_key:
                params["pageKey"] = page_key
            result = _call(api_key, "nr_getAssetTransfers", [params])
            if not isinstance(result, dict):
                raise ConnectorUnavailable("BSCTrace/MegaNode returned an invalid transfer page.")
            rows = result.get("transfers") or []
            if not isinstance(rows, list):
                raise ConnectorUnavailable("BSCTrace/MegaNode returned invalid transfer rows.")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                timestamp = _timestamp(row.get("blockTimestamp", row.get("blockTimeStamp")))
                if since and timestamp is not None:
                    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
                    if timestamp < int(since_utc.timestamp()):
                        continue
                for payload in _transfer_payloads(row):
                    payload = _enrich_payload(api_key, payload, transaction_cache)
                    # A transaction can contain several token legs. Attach a
                    # wallet fee to only one outgoing leg so reconciliation
                    # does not subtract the same gas cost multiple times.
                    if (
                        payload.get("from", "").lower() == address.lower()
                        and payload.get("_kind") != "contract_call"
                        and not payload.get("_internal")
                        and payload["hash"] not in fee_attached_hashes
                    ):
                        payload["_fee_for_wallet"] = True
                        fee_attached_hashes.add(payload["hash"])
                    key = (
                        payload["hash"],
                        payload["_kind"],
                        str(payload.get("contractAddress") or ""),
                        str(payload.get("tokenID") or payload.get("logIndex") or ""),
                    )
                    if key not in seen:
                        seen.add(key)
                        yield payload
            page_key = result.get("pageKey") or result.get("pageToken")
            if not page_key:
                break
        else:
            raise ConnectorUnavailable("BSCTrace/MegaNode returned more than the supported transfer page limit.")


def fetch_balances(address: str, api_key: str) -> list[Balance]:
    """Read native BNB and all non-zero BEP-20 holdings from MegaNode."""
    balances: list[Balance] = []
    native = _intish(_call(api_key, "eth_getBalance", [address, "latest"]))
    if native:
        balances.append(Balance("BNB", f"{Decimal(native) / Decimal(10**18):.18f}", asset_network="BNB Smart Chain", asset_type="COIN"))

    for page in range(1, _MAX_PAGES + 1):
        result = _call(api_key, "nr_getTokenHoldings", [address, hex(page), hex(100)])
        if not isinstance(result, dict):
            raise ConnectorUnavailable("BSCTrace/MegaNode returned invalid token holdings.")
        details = result.get("details") or []
        if not isinstance(details, list):
            raise ConnectorUnavailable("BSCTrace/MegaNode returned invalid token holdings details.")
        for token in details:
            if not isinstance(token, dict):
                continue
            raw_balance = _intish(token.get("tokenBalance"))
            if not raw_balance:
                continue
            decimals = _intish(token.get("tokenDecimails", token.get("tokenDecimals")), 18)
            try:
                amount = Decimal(raw_balance) / (Decimal(10) ** decimals)
            except (InvalidOperation, ValueError):
                continue
            balances.append(
                Balance(
                    token.get("tokenSymbol") or "UNKNOWN",
                    f"{amount:.{min(decimals, 18)}f}",
                    asset_network="BNB Smart Chain",
                    asset_contract=token.get("tokenAddress"),
                    asset_type="TOKEN",
                )
            )
        if len(details) < 100:
            break
    else:
        raise ConnectorUnavailable("BSCTrace/MegaNode returned more than the supported token holdings page limit.")
    return balances
