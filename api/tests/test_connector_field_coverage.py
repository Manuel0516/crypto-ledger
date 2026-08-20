from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import RawRecord
from app.connectors.binance.live import BinanceLiveConnector
from app.connectors.bitget import BitgetConnector, BitgetLiveConnector
from app.connectors.lightning import LightningConnector
from app.connectors.solana import SolanaAddressConnector


OCCURRED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
SOLANA_OWN = "Own11111111111111111111111111111111111111111"
SOLANA_OTHER = "Other111111111111111111111111111111111111111"
SOLANA_MINT = "Mint11111111111111111111111111111111111111111"


def _solana_tx(*, outgoing: bool = False, token: bool = False) -> dict:
    if token:
        return {
            "slot": 123,
            "transaction": {"message": {"accountKeys": [{"pubkey": SOLANA_OTHER}, {"pubkey": SOLANA_OWN}]}},
            "meta": {
                "fee": 5000,
                "preBalances": [1_000_000_000, 1_000_000_000],
                "postBalances": [1_000_000_000, 1_000_000_000],
                "preTokenBalances": [
                    {"accountIndex": 0, "mint": SOLANA_MINT, "owner": SOLANA_OTHER, "uiTokenAmount": {"amount": "2000000", "decimals": 6}},
                    {"accountIndex": 1, "mint": SOLANA_MINT, "owner": SOLANA_OWN, "uiTokenAmount": {"amount": "0", "decimals": 6}},
                ],
                "postTokenBalances": [
                    {"accountIndex": 0, "mint": SOLANA_MINT, "owner": SOLANA_OTHER, "uiTokenAmount": {"amount": "1000000", "decimals": 6}},
                    {"accountIndex": 1, "mint": SOLANA_MINT, "owner": SOLANA_OWN, "uiTokenAmount": {"amount": "1000000", "decimals": 6}},
                ],
            },
        }

    if outgoing:
        pre = [2_000_000_000, 1_000_000_000]
        post = [1_899_995_000, 1_100_000_000]
    else:
        pre = [1_000_000_000, 1_000_000_000]
        post = [900_000_000, 1_100_000_000]
    account_keys = [
        {"pubkey": SOLANA_OWN},
        {"pubkey": SOLANA_OTHER},
    ] if outgoing else [
        {"pubkey": SOLANA_OTHER},
        {"pubkey": SOLANA_OWN},
    ]
    return {
        "slot": 123,
        "transaction": {"message": {"accountKeys": account_keys}},
        "meta": {"fee": 5000, "preBalances": pre, "postBalances": post},
    }


class ConnectorFieldCoverageTests(unittest.TestCase):
    def test_lightning_all_event_kinds_keep_their_structured_evidence(self) -> None:
        connector = LightningConnector("http://lnd", "00", "Lightning")
        cases = [
            ("payment", {"payment_hash": "payment-hash", "value_msat": "1000"}, "payment-hash"),
            ("invoice", {"r_hash": "invoice-hash", "value_msat": "1000"}, "invoice-hash"),
            ("channel_open", {"channel_point": "funding-hash:0", "capacity": "1000"}, "funding-hash"),
            ("channel_close", {"closing_tx_hash": "closing-hash", "settled_balance": "1000"}, "closing-hash"),
        ]
        for kind, payload, expected_hash in cases:
            with self.subTest(kind=kind):
                payload["_kind"] = kind
                event = connector.normalize(RawRecord("lightning", kind, OCCURRED_AT, payload))
                self.assertEqual(event.tx_hash, expected_hash)

    def test_solana_native_and_token_events_populate_both_address_sides(self) -> None:
        connector = SolanaAddressConnector(SOLANA_OWN, "Solana")

        incoming = connector.normalize(
            RawRecord("solana:wallet", "native-in", OCCURRED_AT, {"tx": _solana_tx(), "_leg": "native"})
        )
        self.assertEqual(incoming.address_from, SOLANA_OTHER)
        self.assertEqual(incoming.address_to, SOLANA_OWN)

        outgoing = connector.normalize(
            RawRecord("solana:wallet", "native-out", OCCURRED_AT, {"tx": _solana_tx(outgoing=True), "_leg": "native"})
        )
        self.assertEqual(outgoing.address_from, SOLANA_OWN)
        self.assertEqual(outgoing.address_to, SOLANA_OTHER)

        token = connector.normalize(
            RawRecord(
                "solana:wallet",
                f"token-in-token-{SOLANA_MINT}",
                OCCURRED_AT,
                {"tx": _solana_tx(token=True), "_leg": "token", "_mint": SOLANA_MINT, "_net": 1_000_000, "_decimals": 6},
            )
        )
        self.assertEqual(token.address_from, SOLANA_OTHER)
        self.assertEqual(token.address_to, SOLANA_OWN)
        self.assertEqual(token.tx_hash, "token-in")

    def test_binance_trade_has_quote_leg_and_income_evidence(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        trade = connector.normalize(
            RawRecord(
                "binance",
                "trade-1",
                OCCURRED_AT,
                {
                    "_kind": "trade",
                    "_symbol": "BTCUSDT",
                    "isBuyer": True,
                    "qty": "0.1",
                    "quoteQty": "5000.00000000",
                    "commission": "0.0001",
                    "commissionAsset": "BTC",
                    "id": 1,
                    "orderId": 2,
                },
            )
        )
        self.assertEqual(trade.asset_symbol, "BTC")
        self.assertEqual(trade.secondary_asset_symbol, "USDT")
        self.assertEqual(trade.secondary_amount, "5000.00000000")
        self.assertEqual(trade.direction, "+")

        dividend = connector.normalize(
            RawRecord("binance", "dividend-1", OCCURRED_AT, {"_kind": "dividend", "asset": "BTC", "amount": "0.01", "tranId": 1234})
        )
        self.assertEqual(dividend.order_id, "1234")

        income = connector.normalize(
            RawRecord(
                "binance",
                "income-1",
                OCCURRED_AT,
                {"_kind": "futures_income", "incomeType": "FUNDING_FEE", "asset": "USDT", "income": "-0.25", "tranId": 5678},
            )
        )
        self.assertEqual(income.order_id, "5678")

    def test_bitget_live_fill_has_quote_leg_and_current_fee_shape(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "fill-1",
                OCCURRED_AT,
                {
                    "_kind": "fill",
                    "symbol": "BTCUSDT",
                    "side": "sell",
                    "size": "0.1",
                    "amount": "5000",
                    "tradeId": "t1",
                    "orderId": "o1",
                    "feeDetail": {"feeCoin": "BTC", "totalFee": "-0.0001"},
                },
            )
        )
        self.assertEqual(event.asset_symbol, "BTC")
        self.assertEqual(event.secondary_asset_symbol, "USDT")
        self.assertEqual(event.secondary_amount, "5000")
        self.assertEqual(event.direction, "-")
        self.assertEqual(event.fees[0].asset_symbol, "BTC")
        self.assertEqual(event.fees[0].amount, "0.0001")

    def test_legacy_bitget_import_preserves_evidence_fields(self) -> None:
        connector = BitgetConnector()
        event = connector.normalize(
            RawRecord(
                "bitget",
                "legacy-1",
                OCCURRED_AT,
                {
                    "type": "withdrawal",
                    "timestamp": OCCURRED_AT.isoformat(),
                    "coin": "BTC",
                    "amount": "0.1",
                    "tx_hash": "chain-hash",
                    "order_id": "order-1",
                    "address_from": "wallet-from",
                    "address_to": "wallet-to",
                    "trade_id": "trade-1",
                    "withdrawal_id": "withdrawal-1",
                },
            )
        )
        self.assertEqual(event.tx_hash, "chain-hash")
        self.assertEqual(event.order_id, "order-1")
        self.assertEqual(event.address_from, "wallet-from")
        self.assertEqual(event.address_to, "wallet-to")
        self.assertEqual(event.trade_id, "trade-1")
        self.assertEqual(event.withdrawal_id, "withdrawal-1")


if __name__ == "__main__":
    unittest.main()
