from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.accounts import _validate_chain_network
from app.connectors.base import RawRecord
from app.connectors.evm.connector import CHAINS, EVMAddressConnector
from app.core.assets.registry import KNOWN_ASSETS


ADDRESS = "0x1111111111111111111111111111111111111111"
OTHER_ADDRESS = "0x2222222222222222222222222222222222222222"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
OCCURRED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, result: list[dict]):
        self._result = result

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"message": "OK", "result": self._result}


class EVMConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc")

    def test_bsc_chain_and_native_asset_metadata(self) -> None:
        endpoint, network = CHAINS["bsc"]

        self.assertEqual(endpoint, "https://api.routescan.io/v2/network/mainnet/evm/56/etherscan/api")
        self.assertEqual(network, "BNB Smart Chain")
        self.assertEqual(KNOWN_ASSETS["BNB"]["network"], "BNB Smart Chain")
        self.assertEqual(KNOWN_ASSETS["BNB"]["coingecko_id"], "binancecoin")
        self.assertEqual(KNOWN_ASSETS["BNB"]["decimals"], 18)

    def test_api_accepts_bsc_and_rejects_unknown_evm_networks(self) -> None:
        _validate_chain_network("evm_address", "bsc")

        with self.assertRaises(HTTPException):
            _validate_chain_network("evm_address", "not-a-chain")

    def test_fetch_uses_bsc_namespace_and_all_transfer_endpoints(self) -> None:
        tx = {
            "hash": "0xnative",
            "timeStamp": "1787227200",
            "isError": "0",
            "value": "1000000000000000000",
            "input": "0x",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "gasUsed": "21000",
            "gasPrice": "1000000000",
        }
        token = {
            "hash": "0xtoken",
            "timeStamp": "1787227200",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "value": "1250000",
            "tokenDecimal": "6",
            "tokenSymbol": "USDT",
            "tokenName": "Tether USD",
            "contractAddress": "0x3333333333333333333333333333333333333333",
            "logIndex": "4",
        }
        nft721 = {
            "hash": "0xnft721",
            "timeStamp": "1787227200",
            "from": ZERO_ADDRESS,
            "to": ADDRESS,
            "tokenID": "7",
            "tokenSymbol": "NFT",
            "tokenName": "Example NFT",
            "contractAddress": "0x4444444444444444444444444444444444444444",
            "logIndex": "5",
        }
        nft1155 = {
            "hash": "0xnft1155",
            "timeStamp": "1787227200",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "tokenID": "8",
            "tokenValue": "3",
            "tokenSymbol": "ITEM",
            "tokenName": "Example Item",
            "contractAddress": "0x5555555555555555555555555555555555555555",
            "logIndex": "6",
        }
        responses = {"txlist": [tx], "tokentx": [token], "tokennfttx": [nft721], "token1155tx": [nft1155]}

        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            self.assertEqual(url, CHAINS["bsc"][0])
            self.assertEqual(timeout, 15.0)
            self.assertEqual(params["address"], ADDRESS)
            return FakeResponse(responses[params["action"]])

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            records = list(self.connector.fetch(since=OCCURRED_AT))

        self.assertEqual(len(records), 4)
        self.assertTrue(all(record.source_id == f"evm:bsc:{ADDRESS}" for record in records))
        self.assertEqual({record.payload["_kind"] for record in records}, {"native", "token", "nft721", "nft1155"})

        native = self.connector.normalize(records[0])
        self.assertEqual(native.asset_symbol, "BNB")
        self.assertEqual(native.asset_network, "BNB Smart Chain")
        self.assertEqual(native.amount, "1.000000000000000000")

    def test_bsc_contract_call_records_bnb_gas(self) -> None:
        raw = RawRecord(
            source_id="evm:bsc:test",
            external_id="0xcall",
            source_timestamp=OCCURRED_AT,
            payload={
                "_kind": "contract_call",
                "_network": "BNB Smart Chain",
                "hash": "0xcall",
                "from": ADDRESS,
                "to": OTHER_ADDRESS,
                "gasUsed": "21000",
                "gasPrice": "1000000000",
                "blockNumber": "123",
            },
        )

        event = self.connector.normalize(raw)

        self.assertEqual(event.asset_symbol, "BNB")
        self.assertEqual(event.asset_network, "BNB Smart Chain")
        self.assertEqual(event.event_type, "UNKNOWN")
        self.assertEqual(event.status, "REQUIRES_REVIEW")
        self.assertEqual(len(event.fees), 1)
        self.assertEqual(event.fees[0].asset_symbol, "BNB")
        self.assertEqual(event.fees[0].amount, "0.000021000000000000")

    def test_bsc_token_and_nft_normalization_preserves_network(self) -> None:
        token_raw = RawRecord(
            source_id="evm:bsc:test",
            external_id="0xtoken",
            source_timestamp=OCCURRED_AT,
            payload={
                "_kind": "token",
                "_network": "BNB Smart Chain",
                "hash": "0xtoken",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "value": "1250000",
                "tokenDecimal": "6",
                "tokenSymbol": "USDT",
                "tokenName": "Tether USD",
                "contractAddress": "0x3333333333333333333333333333333333333333",
                "logIndex": "4",
            },
        )
        nft_raw = RawRecord(
            source_id="evm:bsc:test",
            external_id="0xnft",
            source_timestamp=OCCURRED_AT,
            payload={
                "_kind": "nft1155",
                "_network": "BNB Smart Chain",
                "hash": "0xnft",
                "from": OTHER_ADDRESS,
                "to": ADDRESS,
                "tokenID": "8",
                "tokenValue": "3",
                "tokenSymbol": "ITEM",
                "tokenName": "Example Item",
                "contractAddress": "0x5555555555555555555555555555555555555555",
                "logIndex": "6",
            },
        )

        token = self.connector.normalize(token_raw)
        nft = self.connector.normalize(nft_raw)

        self.assertEqual(token.asset_symbol, "USDT")
        self.assertEqual(token.asset_network, "BNB Smart Chain")
        self.assertEqual(token.asset_contract, "0x3333333333333333333333333333333333333333")
        self.assertEqual(token.amount, "1.250000")
        self.assertEqual(nft.asset_network, "BNB Smart Chain")
        self.assertEqual(nft.asset_type, "NFT")
        self.assertEqual(nft.amount, "3")
        self.assertEqual(nft.event_type, "NFT_TRANSFER")


if __name__ == "__main__":
    unittest.main()
