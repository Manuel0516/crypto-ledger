from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import ConnectorUnavailable, RawRecord
from app.connectors.solana.connector import SolanaAddressConnector

ADDRESS = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
OTHER_ADDRESS = "3Kzz9v5r5m1qk8dq6f1y5s6hM8vQeR2m3G4h5J6k7L8m"


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=httpx.Request("POST", "https://x"), response=httpx.Response(self.status_code))

    def json(self):
        return self._payload


def _batch_response(results: list) -> FakeResponse:
    return FakeResponse([{"jsonrpc": "2.0", "id": i, "result": result} for i, result in enumerate(results)])


def _native_tx(*, pre: int, post: int, fee: int = 5000, other: str = OTHER_ADDRESS) -> dict:
    return {
        "slot": 100,
        "blockTime": 1700000000,
        "transaction": {"message": {"accountKeys": [ADDRESS, other]}},
        "meta": {
            "fee": fee,
            "preBalances": [pre, 5_000_000_000],
            "postBalances": [post, 5_000_000_000 - (post - pre) - fee],
        },
    }


class SolanaConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = SolanaAddressConnector(ADDRESS, "SOL wallet")

    def test_rpc_batch_sends_one_request_for_several_calls_and_matches_by_id(self) -> None:
        with patch("app.connectors.solana.connector.httpx.post", return_value=_batch_response(["a", "b", "c"])) as mock_post:
            results = self.connector._rpc_batch([("m1", []), ("m2", []), ("m3", [])])
        self.assertEqual(results, ["a", "b", "c"])
        self.assertEqual(mock_post.call_count, 1)

    def test_rpc_batch_retries_the_whole_request_on_a_per_item_429_error(self) -> None:
        rate_limited = FakeResponse([{"jsonrpc": "2.0", "id": 0, "error": {"code": 429, "message": "Too many requests for a specific RPC call"}}])
        ok = _batch_response(["result"])
        with patch("app.connectors.solana.connector.httpx.post", side_effect=[rate_limited, ok]) as mock_post, patch("app.connectors.solana.connector.time.sleep"):
            results = self.connector._rpc_batch([("getTransaction", ["sig"])])
        self.assertEqual(results, ["result"])
        self.assertEqual(mock_post.call_count, 2)

    def test_rpc_batch_retries_on_http_level_429(self) -> None:
        with patch(
            "app.connectors.solana.connector.httpx.post",
            side_effect=[FakeResponse({}, status_code=429), _batch_response(["result"])],
        ) as mock_post, patch("app.connectors.solana.connector.time.sleep"):
            results = self.connector._rpc_batch([("getBalance", [ADDRESS])])
        self.assertEqual(results, ["result"])
        self.assertEqual(mock_post.call_count, 2)

    def test_rpc_batch_gives_up_after_persistent_rate_limiting(self) -> None:
        always_limited = FakeResponse([{"jsonrpc": "2.0", "id": 0, "error": {"code": 429, "message": "still limited"}}])
        with patch("app.connectors.solana.connector.httpx.post", return_value=always_limited), patch("app.connectors.solana.connector.time.sleep"):
            with self.assertRaises(ConnectorUnavailable):
                self.connector._rpc_batch([("getTransaction", ["sig"])])

    def test_rpc_batch_raises_on_a_non_rate_limit_error_without_retrying(self) -> None:
        bad = FakeResponse([{"jsonrpc": "2.0", "id": 0, "error": {"code": -32602, "message": "Invalid param"}}])
        with patch("app.connectors.solana.connector.httpx.post", return_value=bad) as mock_post, patch("app.connectors.solana.connector.time.sleep"):
            with self.assertRaises(ConnectorUnavailable):
                self.connector._rpc_batch([("getTransaction", ["sig"])])
        self.assertEqual(mock_post.call_count, 1)

    def test_fetch_chunks_transaction_lookups_instead_of_one_call_per_signature(self) -> None:
        signatures = [{"signature": f"sig{i}"} for i in range(7)]
        tx = _native_tx(pre=1_000_000_000, post=1_000_500_000 - 5000)
        calls = []

        def side_effect(url, json=None, **kwargs):
            calls.append(json)
            if json[0]["method"] == "getSignaturesForAddress":
                return FakeResponse([{"jsonrpc": "2.0", "id": 0, "result": signatures if not calls[:-1] or len(calls) == 1 else []}])
            return _batch_response([tx] * len(json))

        with patch("app.connectors.solana.connector.httpx.post", side_effect=side_effect), patch("app.connectors.solana.connector.time.sleep"):
            list(self.connector.fetch(since=None))

        tx_calls = [c for c in calls if c[0]["method"] == "getTransaction"]
        # 7 signatures at GET_TX_CHUNK_SIZE=5 -> two chunked calls (5 + 2), never one call per signature.
        self.assertEqual(len(tx_calls), 2)
        self.assertEqual(sum(len(c) for c in tx_calls), 7)

    def test_normalize_native_deposit_from_real_shaped_transaction(self) -> None:
        tx = _native_tx(pre=1_000_000_000, post=1_500_000_000, fee=0)
        raw = RawRecord(source_id="solana", external_id="sig123", source_timestamp=None, payload={"tx": tx, "_leg": "native"})

        event = self.connector.normalize(raw)

        self.assertEqual(event.event_type, "DEPOSIT")
        self.assertEqual(event.direction, "+")
        self.assertEqual(event.asset_symbol, "SOL")
        self.assertAlmostEqual(float(event.amount), 0.5, places=6)
        self.assertEqual(event.address_from, OTHER_ADDRESS)
        self.assertEqual(event.address_to, ADDRESS)
        self.assertEqual(event.tx_hash, "sig123")

    def test_normalize_native_withdrawal_attaches_network_fee(self) -> None:
        tx = _native_tx(pre=2_000_000_000, post=1_500_000_000, fee=5000)
        raw = RawRecord(source_id="solana", external_id="sig456", source_timestamp=None, payload={"tx": tx, "_leg": "native"})

        event = self.connector.normalize(raw)

        self.assertEqual(event.event_type, "WITHDRAWAL")
        self.assertEqual(event.direction, "-")
        self.assertEqual(event.address_from, ADDRESS)
        self.assertEqual(event.address_to, OTHER_ADDRESS)
        self.assertEqual(len(event.fees), 1)
        self.assertEqual(event.fees[0].fee_type, "NETWORK_FEE")
        self.assertAlmostEqual(float(event.fees[0].amount), 5000 / 1e9, places=9)

    def test_normalize_spl_token_deposit_uses_known_mint_symbol(self) -> None:
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        tx = {
            "slot": 100,
            "transaction": {"message": {"accountKeys": [ADDRESS, OTHER_ADDRESS]}},
            "meta": {
                "preTokenBalances": [{"accountIndex": 0, "owner": ADDRESS, "mint": usdc_mint, "uiTokenAmount": {"amount": "0", "decimals": 6}}],
                "postTokenBalances": [{"accountIndex": 0, "owner": ADDRESS, "mint": usdc_mint, "uiTokenAmount": {"amount": "5000000", "decimals": 6}}],
                "preBalances": [],
                "postBalances": [],
            },
        }
        raw = RawRecord(
            source_id="solana",
            external_id=f"sig789-token-{usdc_mint}",
            source_timestamp=None,
            payload={"tx": tx, "_leg": "token", "_mint": usdc_mint, "_net": 5_000_000, "_decimals": 6},
        )

        event = self.connector.normalize(raw)

        self.assertEqual(event.event_type, "DEPOSIT")
        self.assertEqual(event.asset_symbol, "USDC")
        self.assertEqual(event.asset_contract, usdc_mint)
        self.assertEqual(event.amount, "5.000000")


if __name__ == "__main__":
    unittest.main()
