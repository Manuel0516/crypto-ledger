from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.accounts import _validate_chain_network
from app.connectors.base import ConnectorUnavailable, RawRecord
from app.connectors.evm.connector import CHAINS, EVMAddressConnector, _DEFAULT_BSC_TOKEN_CONTRACTS
from app.core.assets.registry import KNOWN_ASSETS


ADDRESS = "0x1111111111111111111111111111111111111111"
OTHER_ADDRESS = "0x2222222222222222222222222222222222222222"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
OCCURRED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, result, *, status: str = "1", message: str = "OK"):
        self._result = result
        self._status = status
        self._message = message

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"status": self._status, "message": self._message, "result": self._result}


class EVMConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc", config={"explorer_api_key": "test-key"})

    def test_bsc_chain_and_native_asset_metadata(self) -> None:
        endpoint, network = CHAINS["bsc"]

        self.assertEqual(endpoint, "https://api.etherscan.io/v2/api")
        self.assertEqual(network, "BNB Smart Chain")
        self.assertEqual(KNOWN_ASSETS["BNB"]["network"], "BNB Smart Chain")
        self.assertEqual(KNOWN_ASSETS["BNB"]["coingecko_id"], "binancecoin")
        self.assertEqual(KNOWN_ASSETS["BNB"]["decimals"], 18)

    def test_api_accepts_bsc_and_rejects_unknown_evm_networks(self) -> None:
        _validate_chain_network("evm_address", "bsc")
        _validate_chain_network("evm_address", "avalanche")
        _validate_chain_network("evm_address", "custom")

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
            self.assertEqual(timeout, 20.0)
            self.assertEqual(params["address"], ADDRESS)
            self.assertEqual(params["chainid"], "56")
            self.assertEqual(params["apikey"], "test-key")
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

    def test_empty_transfer_messages_are_successful_empty_pages(self) -> None:
        tx = {
            "hash": "0xnative-empty-test",
            "timeStamp": "1787227200",
            "isError": "0",
            "value": "1000000000000000000",
            "input": "0x",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "gasUsed": "21000",
            "gasPrice": "1000000000",
        }
        empty_messages = {
            "tokentx": "No token transfers found",
            "tokennfttx": "No NFT transfers found",
            "token1155tx": "No ERC-1155 transfers found",
        }

        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            if params["action"] == "txlist":
                return FakeResponse([tx])
            return FakeResponse(None, status="0", message=empty_messages[params["action"]])

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            records = list(self.connector.fetch(since=OCCURRED_AT))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload["_kind"], "native")

    def test_provider_error_includes_etherscan_result_detail_and_action(self) -> None:
        # Etherscan places the actionable text in result; model that response
        # directly because a generic NOTOK is not enough for a user to fix a
        # rejected BSC key or rate-limited request.
        def fake_error_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            self.assertEqual(params["action"], "txlist")
            response = FakeResponse("Invalid API Key", status="0", message="NOTOK")
            return response

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_error_get):
            with self.assertRaisesRegex(ConnectorUnavailable, r"txlist.*Invalid API Key"):
                list(self.connector.fetch(since=OCCURRED_AT))

    def test_unsupported_optional_action_does_not_abort_native_history(self) -> None:
        tx = {
            "hash": "0xnative-optional-test",
            "timeStamp": "1787227200",
            "isError": "0",
            "value": "1000000000000000000",
            "input": "0x",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "gasUsed": "21000",
            "gasPrice": "1000000000",
        }

        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            if params["action"] == "txlist":
                return FakeResponse([tx])
            if params["action"] == "tokentx":
                return FakeResponse(None, status="0", message="chain not supported")
            return FakeResponse([])

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            records = list(self.connector.fetch(since=OCCURRED_AT))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload["_kind"], "native")

    def test_fetch_balances_keeps_native_and_token_precision(self) -> None:
        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            self.assertEqual(params["chainid"], "56")
            if params["action"] == "balance":
                return FakeResponse("1234567890123456789")
            if params["action"] == "tokenlist":
                return FakeResponse(
                    [
                        {
                            "type": "ERC-20",
                            "symbol": "USDT",
                            "balance": "1234567",
                            "decimals": "6",
                            "contractAddress": "0x3333333333333333333333333333333333333333",
                        }
                    ]
                )
            self.fail(f"unexpected balance action: {params['action']}")

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            balances = list(self.connector.fetch_balances())

        self.assertEqual([balance.asset_symbol for balance in balances], ["BNB", "USDT"])
        self.assertEqual(balances[0].amount, "1.234567890123456789")
        self.assertEqual(balances[1].amount, "1.234567")

    def test_avalanche_uses_routescan_and_avax(self) -> None:
        connector = EVMAddressConnector(ADDRESS, "Avalanche wallet", chain="avalanche")
        self.assertEqual(CHAINS["avalanche"][0], "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api")
        self.assertEqual(CHAINS["avalanche"][1], "Avalanche")

        tx = {
            "hash": "0xavax",
            "timeStamp": "1787227200",
            "isError": "0",
            "value": "1000000000000000000",
            "input": "0x",
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "gasUsed": "21000",
            "gasPrice": "1000000000",
        }

        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            self.assertEqual(url, CHAINS["avalanche"][0])
            self.assertEqual(params["chainid"], "43114")
            return FakeResponse([tx]) if params["action"] == "txlist" else FakeResponse([])

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            records = list(connector.fetch(since=OCCURRED_AT))

        self.assertEqual(len(records), 1)
        self.assertEqual(connector.normalize(records[0]).asset_symbol, "AVAX")

    def test_custom_network_does_not_fall_back_to_ethereum(self) -> None:
        connector = EVMAddressConnector(
            ADDRESS,
            "Custom wallet",
            chain="custom",
            config={"chain_id": "999", "network_name": "Test EVM", "native_symbol": "TST"},
        )

        def fake_get(url: str, *, params: dict, timeout: float) -> FakeResponse:
            self.assertEqual(url, "https://api.routescan.io/v2/network/mainnet/evm/999/etherscan/api")
            self.assertEqual(params["chainid"], "999")
            return FakeResponse([])

        with patch("app.connectors.evm.connector.httpx.get", side_effect=fake_get):
            self.assertEqual(list(connector.fetch(since=OCCURRED_AT)), [])

    def test_bsc_without_key_tracks_common_tokens_by_default(self) -> None:
        # Etherscan removed BSC from its free tier and no Blockscout instance
        # indexes BSC — there's no free, keyless history API left for it at
        # all, so a keyless BSC account no longer hard-fails; instead it
        # automatically tracks a handful of common BEP-20 contracts (no
        # configuration needed) plus a live native BNB balance.
        connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc")
        self.assertTrue(connector._bsc_public_rpc_mode())
        self.assertEqual(connector._bsc_token_contracts(), list(_DEFAULT_BSC_TOKEN_CONTRACTS))
        with patch("app.connectors.evm.connector.bsc_rpc.fetch_token_transfers", return_value=iter([])) as mocked:
            self.assertEqual(list(connector.fetch(since=OCCURRED_AT)), [])
        mocked.assert_called_once_with(ADDRESS, list(_DEFAULT_BSC_TOKEN_CONTRACTS), OCCURRED_AT)
        self.assertIn("usdc and wbnb", connector.history_limit_note.lower())

    def test_bsc_without_key_adds_a_user_contract_on_top_of_the_defaults(self) -> None:
        connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc", config={"bsc_token_contracts": [OTHER_ADDRESS]})
        fake_transfer = {
            "from": OTHER_ADDRESS,
            "to": ADDRESS,
            "value": "1000000000000000000",
            "contractAddress": OTHER_ADDRESS,
            "hash": "0xabc",
            "logIndex": "0",
            "blockNumber": "123",
            "tokenDecimal": "18",
            "tokenSymbol": "TOKN",
            "tokenName": "TOKN",
            "timeStamp": "1787227200",
        }
        with patch("app.connectors.evm.connector.bsc_rpc.fetch_token_transfers", return_value=iter([fake_transfer])) as mocked:
            records = list(connector.fetch(since=OCCURRED_AT))
        mocked.assert_called_once_with(ADDRESS, [OTHER_ADDRESS, *_DEFAULT_BSC_TOKEN_CONTRACTS], OCCURRED_AT)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload["_kind"], "token")

    def test_bsc_without_key_does_not_duplicate_a_default_the_user_also_listed(self) -> None:
        default_contract = _DEFAULT_BSC_TOKEN_CONTRACTS[0]
        # A checksummed address only mixes case in the hex digits, never in
        # the "0x" prefix — this is the realistic shape of "the same
        # contract, different casing" that dedup needs to handle.
        differently_cased = "0x" + default_contract[2:].upper()
        connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc", config={"bsc_token_contracts": [differently_cased]})
        contracts = connector._bsc_token_contracts()
        self.assertEqual(len(contracts), len(_DEFAULT_BSC_TOKEN_CONTRACTS))
        self.assertEqual(sum(1 for c in contracts if c.lower() == default_contract.lower()), 1)

    def test_bsc_with_key_is_not_in_public_rpc_mode(self) -> None:
        self.assertFalse(self.connector._bsc_public_rpc_mode())
        self.assertIsNone(self.connector.history_limit_note)

    def test_bsc_public_rpc_balances_use_native_lookup_and_configured_contracts(self) -> None:
        connector = EVMAddressConnector(
            ADDRESS, "BSC wallet", chain="bsc", config={"bsc_token_contracts": [OTHER_ADDRESS]}
        )
        with (
            patch("app.connectors.evm.connector.bsc_rpc.native_balance", return_value=Decimal("1.5")),
            patch("app.connectors.evm.connector.bsc_rpc.token_balance", return_value=(Decimal("42"), 18, "TOKN")),
        ):
            balances = list(connector.fetch_balances())
        # 1 native + (OTHER_ADDRESS + every default contract), all mocked
        # to the same fixed (amount, decimals, symbol) tuple.
        self.assertEqual(len(balances), 1 + 1 + len(_DEFAULT_BSC_TOKEN_CONTRACTS))
        native = next(b for b in balances if b.asset_symbol == "BNB")
        token = next(b for b in balances if b.asset_contract == OTHER_ADDRESS)
        self.assertEqual(native.amount, "1.500000000000000000")
        self.assertEqual(token.asset_contract, OTHER_ADDRESS)

    def test_bsc_public_rpc_mode_rejects_an_invalid_configured_contract(self) -> None:
        connector = EVMAddressConnector(ADDRESS, "BSC wallet", chain="bsc", config={"bsc_token_contracts": ["not-an-address"]})
        with self.assertRaises(ConnectorUnavailable):
            list(connector.fetch_balances())

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
