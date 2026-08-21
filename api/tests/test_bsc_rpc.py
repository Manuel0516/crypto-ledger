from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import ConnectorUnavailable
from app.connectors.evm import bsc_rpc

ADDRESS = "0x1111111111111111111111111111111111111111"
CONTRACT = "0x2222222222222222222222222222222222222222"


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def _ok(result):
    return _FakeResponse({"jsonrpc": "2.0", "id": 1, "result": result})


def _err(message: str, code: int = -32005):
    return _FakeResponse({"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": message}})


class BscRpcTests(unittest.TestCase):
    def test_native_balance_converts_wei_hex_to_bnb(self) -> None:
        with patch("app.connectors.evm.bsc_rpc.httpx.post", return_value=_ok("0xde0b6b3a7640000")):  # 1e18 wei
            self.assertEqual(bsc_rpc.native_balance(ADDRESS), Decimal("1"))

    def test_native_balance_of_zero_returns_zero_not_an_error(self) -> None:
        with patch("app.connectors.evm.bsc_rpc.httpx.post", return_value=_ok("0x0")):
            self.assertEqual(bsc_rpc.native_balance(ADDRESS), Decimal("0"))

    def test_falls_back_to_the_next_endpoint_when_the_first_fails(self) -> None:
        import httpx

        calls = {"n": 0}

        def side_effect(url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("down")
            return _ok("0x2540be400")  # 10e9 wei

        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect):
            self.assertEqual(bsc_rpc.native_balance(ADDRESS), Decimal("10") / Decimal(10**9))
        self.assertGreaterEqual(calls["n"], 2)

    def test_all_endpoints_failing_raises_connector_unavailable(self) -> None:
        import httpx

        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=httpx.ConnectError("down")):
            with self.assertRaises(ConnectorUnavailable):
                bsc_rpc.native_balance(ADDRESS)

    def test_json_rpc_error_response_is_treated_like_a_failure_and_tried_on_next_endpoint(self) -> None:
        calls = {"n": 0}

        def side_effect(url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _err("limit exceeded")
            return _ok("0x0")

        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect):
            self.assertEqual(bsc_rpc.native_balance(ADDRESS), Decimal("0"))
        self.assertGreaterEqual(calls["n"], 2)

    def test_token_balance_decodes_amount_decimals_and_symbol(self) -> None:
        # balanceOf -> 42 * 10^18, decimals() -> 18, symbol() -> ABI string "TOKN"
        offset_word = (32).to_bytes(32, "big")
        length_word = (4).to_bytes(32, "big")
        data_word = b"TOKN".ljust(32, b"\x00")
        symbol_hex = "0x" + (offset_word + length_word + data_word).hex()
        responses = iter([
            _ok(hex(42 * 10**18)),
            _ok("0x12"),  # 18
            _ok(symbol_hex),
        ])
        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=lambda url, **kw: next(responses)):
            amount, decimals, symbol = bsc_rpc.token_balance(ADDRESS, CONTRACT)
        self.assertEqual(amount, Decimal("42"))
        self.assertEqual(decimals, 18)
        self.assertEqual(symbol, "TOKN")

    def test_token_balance_tolerates_a_non_standard_symbol_response(self) -> None:
        responses = iter([_ok(hex(5 * 10**6)), _ok("0x6"), _ok("0x")])  # balance, decimals=6, unreadable symbol
        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=lambda url, **kw: next(responses)):
            amount, decimals, symbol = bsc_rpc.token_balance(ADDRESS, CONTRACT)
        self.assertEqual(amount, Decimal("5"))
        self.assertEqual(decimals, 6)
        self.assertIsNone(symbol)


class LogScanningTests(unittest.TestCase):
    def test_estimate_block_biases_earlier_never_later(self) -> None:
        # latest block=1000 at ts=3000; target ts=2000 (1000s / ~333 blocks
        # back at 3s/block) plus the default 3600s margin must never land
        # later than the unmargined estimate.
        unmargined = bsc_rpc._estimate_block_for_timestamp(2000, 1000, 3000, safety_margin_seconds=0)
        margined = bsc_rpc._estimate_block_for_timestamp(2000, 1000, 3000)
        self.assertLessEqual(margined, unmargined)

    def test_decode_transfer_log_extracts_addresses_from_topics(self) -> None:
        log = {
            "address": CONTRACT,
            "topics": [
                bsc_rpc._TRANSFER_TOPIC,
                "0x" + "0" * 24 + ADDRESS[2:],
                "0x" + "0" * 24 + CONTRACT[2:],
            ],
            "data": hex(500),
            "transactionHash": "0xdeadbeef",
            "logIndex": "0x2",
            "blockNumber": "0x64",
        }
        decoded = bsc_rpc._decode_transfer_log(log)
        self.assertEqual(decoded["from"], ADDRESS)
        self.assertEqual(decoded["to"], CONTRACT)
        self.assertEqual(decoded["value"], "500")
        self.assertEqual(decoded["blockNumber"], "100")

    def test_removed_reorged_logs_are_skipped(self) -> None:
        from datetime import datetime, timezone

        latest_hex = hex(10_100)
        latest_block_json = _ok(latest_hex)
        block_time_json = _ok({"timestamp": hex(1_700_000_000)})
        since = datetime.fromtimestamp(1_700_000_000 - 20, tz=timezone.utc)
        reorged_log = {
            "address": CONTRACT,
            "topics": [bsc_rpc._TRANSFER_TOPIC, "0x" + "0" * 24 + CONTRACT[2:], "0x" + "0" * 24 + ADDRESS[2:]],
            "data": hex(1),
            "transactionHash": "0xreorged",
            "logIndex": "0x0",
            "blockNumber": "0x1",
            "removed": True,
        }
        calls = {"n": 0}

        def side_effect(url, json=None, **kw):
            calls["n"] += 1
            method = json["method"]
            if method == "eth_blockNumber":
                return latest_block_json
            if method == "eth_getBlockByNumber":
                return block_time_json
            if method == "eth_getLogs":
                return _ok([reorged_log])
            if method == "eth_call":
                return _ok("0x")
            raise AssertionError(f"unexpected method {method}")

        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect):
            results = list(bsc_rpc.fetch_token_transfers(ADDRESS, [CONTRACT], since))
        self.assertEqual(results, [])

    def test_window_shrinks_on_too_large_error_then_recovers(self) -> None:
        good_log = {
            "address": CONTRACT,
            "topics": [bsc_rpc._TRANSFER_TOPIC, "0x" + "0" * 24 + CONTRACT[2:], "0x" + "0" * 24 + ADDRESS[2:]],
            "data": hex(10**18),
            "transactionHash": "0xgood",
            "logIndex": "0x0",
            "blockNumber": "0x5",
        }
        call_log = []

        def side_effect(url, json=None, **kw):
            method = json["method"]
            if method == "eth_getLogs":
                params = json["params"][0]
                width = int(params["toBlock"], 16) - int(params["fromBlock"], 16) + 1
                call_log.append(width)
                if width > bsc_rpc._MIN_LOG_WINDOW_BLOCKS:
                    return _err("query exceeds max results 20000")
                return _ok([good_log])
            raise AssertionError(f"unexpected method {method}")

        with patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect):
            results = list(
                bsc_rpc._scan_logs_for_topic(CONTRACT, "0x" + "0" * 24 + ADDRESS[2:], incoming=True, from_block=0, to_block=50)
            )
        self.assertTrue(any(w <= bsc_rpc._MIN_LOG_WINDOW_BLOCKS for w in call_log))
        self.assertTrue(len(results) >= 1)

    def test_a_transient_non_size_error_is_retried_in_place_before_switching_endpoints(self) -> None:
        # Real-world case: 1rpc.io returned "header not found" for a call
        # that succeeded moments later against the exact same range — a
        # decentralized relay routing to a different backend node on
        # retry, not a real "this endpoint can't serve this" signal.
        good_log = {
            "address": CONTRACT,
            "topics": [bsc_rpc._TRANSFER_TOPIC, "0x" + "0" * 24 + CONTRACT[2:], "0x" + "0" * 24 + ADDRESS[2:]],
            "data": hex(10**18),
            "transactionHash": "0xgood",
            "logIndex": "0x0",
            "blockNumber": "0x5",
        }
        calls = {"n": 0}

        def side_effect(url, json=None, **kw):
            calls["n"] += 1
            if json["method"] == "eth_getLogs":
                if calls["n"] == 1:
                    return _err("header not found", code=-32000)
                return _ok([good_log])
            raise AssertionError(f"unexpected method {json['method']}")

        with (
            patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect),
            patch("app.connectors.evm.bsc_rpc.time.sleep") as mock_sleep,
        ):
            results = list(
                bsc_rpc._scan_logs_for_topic(CONTRACT, "0x" + "0" * 24 + ADDRESS[2:], incoming=True, from_block=0, to_block=50)
            )
        self.assertEqual(len(results), 1)
        mock_sleep.assert_called_once()

    def test_fetch_token_transfers_with_no_contracts_yields_nothing(self) -> None:
        self.assertEqual(list(bsc_rpc.fetch_token_transfers(ADDRESS, [], None)), [])

    def test_one_contract_failing_does_not_lose_another_contracts_data(self) -> None:
        # Real-world case that motivated this: public infra had a transient
        # hiccup scanning one of several default-tracked contracts. That
        # must not cost every other (working) contract its data for the
        # round — each (contract, direction) pair degrades independently,
        # and only after every pair is attempted does a real failure
        # surface, so the caller still keeps everything that succeeded.
        from datetime import datetime, timezone

        bad_contract = CONTRACT
        good_contract = "0x3333333333333333333333333333333333333333"
        since = datetime.fromtimestamp(1_700_000_000 - 20, tz=timezone.utc)
        good_log = {
            "address": good_contract,
            "topics": [bsc_rpc._TRANSFER_TOPIC, "0x" + "0" * 24 + good_contract[2:], "0x" + "0" * 24 + ADDRESS[2:]],
            "data": hex(10**18),
            "transactionHash": "0xgood",
            "logIndex": "0x0",
            "blockNumber": "0x5",
        }

        def side_effect(url, json=None, **kw):
            method = json["method"]
            if method == "eth_blockNumber":
                return _ok(hex(10_100))
            if method == "eth_getBlockByNumber":
                return _ok({"timestamp": hex(1_700_000_000)})
            if method == "eth_getLogs":
                contract = json["params"][0]["address"]
                if contract == bad_contract:
                    return _err("boom", code=-32000)  # not a "too large" marker — exhausts both endpoints fast
                if contract == good_contract and json["params"][0]["topics"][1] is None:
                    return _ok([good_log])  # incoming scan only
                return _ok([])
            if method == "eth_call":
                return _ok("0x")
            raise AssertionError(f"unexpected method {method}")

        with (
            patch("app.connectors.evm.bsc_rpc.httpx.post", side_effect=side_effect),
            patch("app.connectors.evm.bsc_rpc.time.sleep"),
        ):
            gen = bsc_rpc.fetch_token_transfers(ADDRESS, [bad_contract, good_contract], since)
            yielded = []
            with self.assertRaises(ConnectorUnavailable) as ctx:
                for item in gen:
                    yielded.append(item)

        self.assertEqual(len(yielded), 1)
        self.assertEqual(yielded[0]["contractAddress"], good_contract)
        self.assertIn(bad_contract, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
