from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.evm import bsctrace
from app.connectors.base import ConnectorUnavailable


ADDRESS = "0x1111111111111111111111111111111111111111"
OTHER_ADDRESS = "0x2222222222222222222222222222222222222222"
CONTRACT = "0x3333333333333333333333333333333333333333"
API_KEY = "trace-key"
OCCURRED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


def _ok(result):
    return _FakeResponse({"jsonrpc": "2.0", "id": 1, "result": result})


class BscTraceTests(unittest.TestCase):
    def test_estimated_range_never_starts_at_latest_block(self) -> None:
        with patch("app.connectors.evm.bsctrace._call", return_value=1000), patch.object(bsctrace, "_BLOCK_SECONDS", 3600):
            from_block = bsctrace._estimated_from_block(
                API_KEY,
                datetime.now(timezone.utc).replace(year=2030),
            )
        self.assertEqual(from_block, 999)

    def test_fetch_transfers_uses_a_numeric_increasing_block_range(self) -> None:
        calls: list[dict] = []

        def side_effect(url, json=None, **kwargs):
            calls.append(json)
            if json["method"] == "eth_blockNumber":
                return _ok("0x100")
            self.assertEqual(json["method"], "nr_getAssetTransfers")
            params = json["params"][0]
            self.assertLess(int(params["fromBlock"], 16), int(params["toBlock"], 16))
            return _ok({"transfers": []})

        with patch("app.connectors.evm.bsctrace.httpx.post", side_effect=side_effect):
            self.assertEqual(list(bsctrace.fetch_transfers(ADDRESS, API_KEY, since=OCCURRED_AT)), [])
        self.assertEqual(len(calls), 3)  # block estimate + one page for each direction

    def test_fetch_transfers_maps_all_indexed_categories_and_deduplicates_directions(self) -> None:
        timestamp = int(OCCURRED_AT.timestamp())
        rows = [
            {
                "category": "external",
                "blockNum": "0x64",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "value": hex(10**18),
                "asset": "BNB",
                "hash": "0xnative",
                "blockTimestamp": timestamp,
                "gasPrice": "0x3b9aca00",
                "gasUsed": "0x5208",
                "receiptsStatus": 1,
            },
            {
                "category": "internal",
                "blockNum": "0x65",
                "from": ADDRESS,
                "to": OTHER_ADDRESS,
                "value": hex(2 * 10**18),
                "asset": "BNB",
                "hash": "0xinternal",
                "blockTimestamp": timestamp,
            },
            {
                "category": "20",
                "blockNum": "0x66",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "value": hex(1_250_000),
                "asset": "USDC",
                "hash": "0xtoken",
                "contractAddress": CONTRACT,
                "decimal": "6",
                "blockTimestamp": timestamp,
            },
            {
                "category": "721",
                "blockNum": "0x67",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "value": "0x0",
                "asset": "NFT",
                "hash": "0xnft",
                "contractAddress": CONTRACT,
                "erc721TokenId": "0x2a",
                "blockTimestamp": timestamp,
            },
            {
                "category": "1155",
                "blockNum": "0x68",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "value": "0x0",
                "asset": "ITEM",
                "hash": "0x1155",
                "contractAddress": CONTRACT,
                "erc1155Metadata": [{"tokenId": "0x7", "value": "0x3"}],
                "blockTimestamp": timestamp,
            },
            {
                "category": "external",
                "blockNum": "0x69",
                "from": ADDRESS,
                "to": CONTRACT,
                "value": "0x0",
                "asset": "BNB",
                "hash": "0xcall",
                "blockTimestamp": timestamp,
                "gasPrice": "0x3b9aca00",
                "gasUsed": "0x5208",
            },
        ]
        calls: list[dict] = []

        def side_effect(url, json=None, **kwargs):
            calls.append(json)
            self.assertEqual(url, f"{bsctrace.BASE_URL}/{API_KEY}")
            if json["method"] == "eth_blockNumber":
                return _ok("0x100")
            if json["method"] == "nr_getTransactionDetail":
                return _ok(
                    {
                        "blockHash": "0xblock",
                        "fees": 21000000000000,
                        "ethereumSpecific": {
                            "gasUsed": 21000,
                            "gasPrice": 1000000000,
                            "input": "0x1234",
                            "nonce": 7,
                            "transactionIndex": 2,
                        },
                    }
                )
            self.assertEqual(json["method"], "nr_getAssetTransfers")
            params = json["params"][0]
            if "fromAddress" in params and not params.get("pageKey"):
                return _ok({"transfers": rows[:3], "pageKey": "next"})
            if "fromAddress" in params:
                return _ok({"transfers": rows[3:]})
            if "toAddress" in params:
                return _ok({"transfers": [rows[0]]})
            raise AssertionError("unexpected transfer filter")

        with patch("app.connectors.evm.bsctrace.httpx.post", side_effect=side_effect):
            payloads = list(bsctrace.fetch_transfers(ADDRESS, API_KEY, since=OCCURRED_AT))

        self.assertEqual(len(payloads), 6)
        self.assertEqual({payload["_kind"] for payload in payloads}, {"native", "token", "nft721", "nft1155", "contract_call"})
        token = next(payload for payload in payloads if payload["_kind"] == "token")
        self.assertEqual(token["value"], "1250000")
        self.assertEqual(token["tokenDecimal"], "6")
        self.assertEqual(token["blockHash"], "0xblock")
        self.assertEqual(token["_transaction_fee_wei"], "21000000000000")
        nft = next(payload for payload in payloads if payload["_kind"] == "nft721")
        self.assertEqual(nft["tokenID"], "42")
        item = next(payload for payload in payloads if payload["_kind"] == "nft1155")
        self.assertEqual(item["tokenValue"], "3")
        self.assertEqual(len(calls), 10)  # block estimate + three transfer pages + six detail lookups

    def test_since_filters_old_indexed_rows(self) -> None:
        old = {"category": "external", "from": OTHER_ADDRESS, "to": ADDRESS, "value": "0x1", "hash": "0xold", "blockTimestamp": int(OCCURRED_AT.timestamp()) - 1}
        current = {**old, "hash": "0xcurrent", "blockTimestamp": int(OCCURRED_AT.timestamp())}

        def side_effect(url, json=None, **kwargs):
            method = json["method"]
            if method == "eth_blockNumber":
                return _ok("0x100")
            if method == "nr_getTransactionDetail":
                return _ok({"blockHash": "0xblock", "fees": 1, "ethereumSpecific": {"gasUsed": 1, "gasPrice": 1}})
            if method == "nr_getAssetTransfers":
                return _ok({"transfers": [old, current]})
            raise AssertionError(method)

        with patch("app.connectors.evm.bsctrace.httpx.post", side_effect=side_effect):
            payloads = list(bsctrace.fetch_transfers(ADDRESS, API_KEY, since=OCCURRED_AT))
        self.assertEqual([payload["hash"] for payload in payloads], ["0xcurrent"])

    def test_fetch_balances_uses_native_and_token_holdings(self) -> None:
        def side_effect(url, json=None, **kwargs):
            method = json["method"]
            if method == "eth_getBalance":
                return _ok(hex(2 * 10**18))
            if method == "nr_getTokenHoldings":
                return _ok(
                    {
                        "totalCount": "0x1",
                        "details": [
                            {
                                "tokenAddress": CONTRACT,
                                "tokenSymbol": "USDC",
                                "tokenDecimails": "0x6",
                                "tokenBalance": hex(1_250_000),
                            }
                        ],
                    }
                )
            raise AssertionError(method)

        with patch("app.connectors.evm.bsctrace.httpx.post", side_effect=side_effect):
            balances = bsctrace.fetch_balances(ADDRESS, API_KEY)
        self.assertEqual(len(balances), 2)
        self.assertEqual(balances[0].amount, "2.000000000000000000")
        self.assertEqual(balances[1].asset_symbol, "USDC")
        self.assertEqual(balances[1].amount, "1.250000")

    def test_provider_error_does_not_expose_the_api_key(self) -> None:
        with patch(
            "app.connectors.evm.bsctrace.httpx.post",
            return_value=_FakeResponse({"jsonrpc": "2.0", "id": 1, "error": {"message": "invalid key"}}),
        ):
            with self.assertRaisesRegex(ConnectorUnavailable, "invalid key") as caught:
                bsctrace.fetch_balances(ADDRESS, API_KEY)
        self.assertNotIn(API_KEY, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
