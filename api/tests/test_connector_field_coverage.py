from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import ConnectorUnavailable, RawRecord
from app.connectors.binance.live import BinanceLiveConnector, _ms, _seconds
from app.connectors.bitget import BitgetConnector, BitgetLiveConnector
from app.connectors.bitget.live import _is_additional_margin_financial, _is_additional_uta_financial
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
    def test_binance_timestamps_accept_milliseconds_seconds_and_date_strings(self) -> None:
        expected = datetime(2024, 10, 6, 8, 29, 51, tzinfo=timezone.utc)

        self.assertEqual(_ms(int(expected.timestamp() * 1000)), expected)
        self.assertEqual(_seconds(int(expected.timestamp())), expected)
        self.assertEqual(_ms("2024-10-06 08:29:51"), expected)
        self.assertEqual(_ms("2024-10-06T08:29:51Z"), expected)

    def test_binance_signed_error_envelope_surfaces_provider_code_and_message(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")

        response = httpx.Response(
            200,
            json={"code": -2015, "msg": "Invalid API-key, IP, or permissions for action."},
            request=httpx.Request("GET", "https://api.binance.com/api/v3/account"),
        )
        with patch("app.connectors.binance.live.httpx.get", return_value=response):
            with self.assertRaisesRegex(ConnectorUnavailable, r"/api/v3/account.*-2015.*Invalid API-key"):
                connector._signed_get("https://api.binance.com", "/api/v3/account")

    def test_binance_signed_non_json_response_is_actionable(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")

        response = httpx.Response(
            200,
            content=b"upstream returned HTML",
            request=httpx.Request("GET", "https://api.binance.com/api/v3/account"),
        )
        with patch("app.connectors.binance.live.httpx.get", return_value=response):
            with self.assertRaisesRegex(ConnectorUnavailable, r"non-JSON response.*account"):
                connector._signed_get("https://api.binance.com", "/api/v3/account")

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

    def test_binance_simple_earn_reads_rows_envelopes_and_locked_rewards(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        with patch.object(
            connector,
            "_signed_get",
            return_value={"total": 2, "rows": [{"purchaseId": "p1"}, {"purchaseId": "p2"}]},
        ):
            rows = list(connector._simple_earn_history("/sapi/v1/simple-earn/flexible/history/subscriptionRecord", OCCURRED_AT))
        self.assertEqual([row["purchaseId"] for row in rows], ["p1", "p2"])

        locked_reward = connector.normalize(
            RawRecord(
                "binance",
                "earn-locked-reward-1",
                OCCURRED_AT,
                {"_kind": "earn_reward", "_earn_product": "locked", "asset": "SOL", "rewards": "0.012", "positionId": "pos-1"},
            )
        )
        self.assertEqual((locked_reward.event_type, locked_reward.event_subtype, locked_reward.amount), ("STAKING_REWARD", "simple_earn_locked", "0.012"))

    def test_binance_margin_interest_is_paged_and_normalized_as_a_cost(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        with patch.object(
            connector,
            "_signed_get",
            return_value={
                "total": 1,
                "rows": [
                    {
                        "txId": 42,
                        "asset": "USDT",
                        "interest": "0.02414667",
                        "interestAccuredTime": 1_723_998_400_000,
                        "principal": "36.22",
                        "type": "PERIODIC",
                        "isolatedSymbol": "BTCUSDT",
                    }
                ],
            },
        ) as request:
            rows = list(connector._margin_interest_history(OCCURRED_AT))
        self.assertEqual(rows[0]["txId"], 42)
        self.assertEqual(request.call_args.args[1], "/sapi/v1/margin/interestHistory")
        self.assertEqual(request.call_args.args[2]["size"], "100")

        event = connector.normalize(RawRecord("binance", "margin-interest-42", OCCURRED_AT, {"_kind": "margin_interest", **rows[0]}))
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("MARGIN_INTEREST", "-", "USDT", "0.02414667"))
        self.assertEqual(event.order_id, "42")

    def test_binance_spot_trade_cursor_and_historical_asset_discovery(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        first_page = [{"id": index} for index in range(1000)]
        with patch.object(connector, "_signed_get_optional", side_effect=[first_page, [{"id": 1000}]]) as request:
            records = list(connector._spot_trades("SOLUSDT", OCCURRED_AT))
        self.assertEqual(len(records), 1001)
        self.assertEqual(request.call_args_list[1].args[2]["fromId"], "1000")
        self.assertIn("SOLUSDT", connector._symbols_from_assets(["SOL"]))

    def test_binance_margin_trades_cover_cross_and_isolated_execution_ledgers(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        with patch.object(connector, "_signed_get_optional", return_value=[{"id": 9}]) as request:
            trades = list(connector._margin_trades("ETHUSDT", OCCURRED_AT, isolated=True))
        self.assertEqual(trades, [{"id": 9}])
        self.assertEqual(request.call_args.args[1], "/sapi/v1/margin/myTrades")
        self.assertEqual(request.call_args.args[2]["isIsolated"], "TRUE")

        event = connector.normalize(
            RawRecord(
                "binance",
                "margin-trade-isolated-ETHUSDT-9",
                OCCURRED_AT,
                {
                    "_kind": "margin_trade",
                    "_symbol": "ETHUSDT",
                    "_is_isolated": True,
                    "id": 9,
                    "orderId": 10,
                    "isBuyer": False,
                    "qty": "0.2",
                    "quoteQty": "600",
                    "commission": "0.1",
                    "commissionAsset": "USDT",
                },
            )
        )
        self.assertEqual((event.event_type, event.event_subtype, event.direction), ("SELL", "margin_isolated", "-"))
        self.assertEqual(event.secondary_asset_symbol, "USDT")

    def test_binance_sol_staking_keeps_receipt_token_and_reward_evidence(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        stake = connector.normalize(
            RawRecord(
                "binance",
                "sol-stake-1",
                OCCURRED_AT,
                {"_kind": "sol_stake", "asset": "SOL", "amount": "4", "distributeAsset": "BNSOL", "distributeAmount": "3.92", "status": "SUCCESS"},
            )
        )
        self.assertEqual((stake.event_type, stake.asset_symbol, stake.secondary_asset_symbol, stake.secondary_amount), ("STAKING_DEPOSIT", "SOL", "BNSOL", "3.92"))

        reward = connector.normalize(
            RawRecord(
                "binance",
                "bnsol-reward-1",
                OCCURRED_AT,
                {"_kind": "bnsol_reward", "amountInSOL": "0.003", "annualPercentageRate": "0.075", "status": "SUCCESS"},
            )
        )
        self.assertEqual((reward.event_type, reward.asset_symbol, reward.amount), ("STAKING_REWARD", "SOL", "0.003"))

    def test_binance_c2c_trade_has_fiat_counter_leg_wallet_and_fee(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance",
                "c2c-1",
                OCCURRED_AT,
                {
                    "_kind": "c2c", "orderNumber": "c2c-1", "tradeType": "SELL", "asset": "USDT", "amount": "100",
                    "fiat": "EUR", "totalPrice": "92", "commission": "0.1", "counterPartNickName": "buyer***",
                    "advertisementRole": "TAKER",
                },
            )
        )
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("SELL", "-", "USDT", "100"))
        self.assertEqual((event.secondary_asset_symbol, event.secondary_amount, event.address_to), ("EUR", "92", "buyer***"))
        self.assertEqual((event.fees[0].asset_symbol, event.fees[0].amount), ("USDT", "0.1"))

    def test_binance_auto_invest_has_both_execution_legs_and_fee(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "auto-invest-1", OCCURRED_AT,
                {
                    "_kind": "auto_invest", "id": 1, "planName": "Weekly BTC", "transactionStatus": "SUCCESS",
                    "sourceAsset": "USDT", "sourceAssetAmount": "100", "targetAsset": "BTC",
                    "targetAssetAmount": "0.0016", "transactionFee": "0.00001", "transactionFeeUnit": "BTC",
                },
            )
        )
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("BUY", "+", "BTC", "0.0016"))
        self.assertEqual((event.secondary_asset_symbol, event.secondary_amount), ("USDT", "100"))
        self.assertEqual((event.fees[0].asset_symbol, event.fees[0].amount), ("BTC", "0.00001"))

    def test_binance_auto_invest_pages_the_documented_thirty_day_windows(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        with patch.object(
            connector,
            "_signed_get_optional_object",
            return_value={"total": 2, "rows": [{"id": 1}, {"id": 2}]},
        ) as request:
            records = list(connector._auto_invest_history(OCCURRED_AT))
        self.assertEqual([row["id"] for row in records], [1, 2])
        self.assertEqual(request.call_args.args[1], "/sapi/v1/lending/auto-invest/history/list")
        self.assertEqual(request.call_args.args[2]["size"], "100")

    def test_binance_crypto_loan_preserves_borrow_collateral_repayment_and_ltv_adjustment(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        borrow = connector.normalize(
            RawRecord(
                "binance", "crypto-loan-borrow-1", OCCURRED_AT,
                {
                    "_kind": "crypto_loan_borrow", "orderId": 1, "loanCoin": "USDT", "initialLoanAmount": "500",
                    "collateralCoin": "ETH", "initialCollateralAmount": "0.2", "loanTerm": "30", "status": "Repaid",
                },
            )
        )
        repay = connector.normalize(
            RawRecord(
                "binance", "crypto-loan-repay-1", OCCURRED_AT,
                {
                    "_kind": "crypto_loan_repay", "orderId": 1, "loanCoin": "USDT", "repayAmount": "500",
                    "collateralCoin": "ETH", "collateralReturn": "0.2", "repayType": "1", "repayStatus": "Repaid",
                },
            )
        )
        adjustment = connector.normalize(
            RawRecord(
                "binance", "crypto-loan-adjustment-1", OCCURRED_AT,
                {"_kind": "crypto_loan_adjustment", "orderId": 1, "collateralCoin": "ETH", "amount": "0.05", "direction": "ADDITIONAL"},
            )
        )
        self.assertEqual((borrow.event_type, borrow.asset_symbol, borrow.secondary_asset_symbol, borrow.secondary_amount), ("MARGIN_BORROW", "USDT", "ETH", "0.2"))
        self.assertEqual((repay.event_type, repay.direction, repay.secondary_asset_symbol, repay.secondary_amount), ("MARGIN_REPAY", "-", "ETH", "0.2"))
        self.assertEqual((adjustment.event_type, adjustment.direction, adjustment.amount), ("LENDING_DEPOSIT", "-", "0.05"))

    def test_binance_crypto_loan_collateral_repayment_is_not_misclassified_as_spot_repay(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "crypto-loan-repay-collateral", OCCURRED_AT,
                {
                    "_kind": "crypto_loan_repay", "orderId": 2, "loanCoin": "USDT", "repayAmount": "500",
                    "collateralCoin": "ETH", "collateralUsed": "0.17", "repayType": "2", "repayStatus": "Repaid",
                },
            )
        )
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("LIQUIDATION", "-", "ETH", "0.17"))

    def test_binance_flexible_loan_adjustment_and_liquidation_use_their_documented_fields(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        adjustment = connector.normalize(
            RawRecord(
                "binance", "flexible-loan-adjustment-1", OCCURRED_AT,
                {"_kind": "crypto_loan_adjustment", "_loan_mode": "flexible", "collateralCoin": "BNB", "collateralAmount": "2.5", "direction": "REDUCED"},
            )
        )
        liquidation = connector.normalize(
            RawRecord(
                "binance", "flexible-loan-liquidation-1", OCCURRED_AT,
                {
                    "_kind": "crypto_loan_liquidation", "loanCoin": "USDT", "collateralCoin": "BNB",
                    "liquidationCollateralAmount": "3", "returnCollateralAmount": "0.2", "liquidationFee": "0.01", "status": "Liquidated",
                },
            )
        )
        self.assertEqual((adjustment.event_type, adjustment.direction, adjustment.amount), ("LENDING_WITHDRAWAL", "+", "2.5"))
        self.assertEqual((liquidation.event_type, liquidation.asset_symbol, liquidation.amount, liquidation.secondary_amount), ("LIQUIDATION", "BNB", "3", "0.2"))
        self.assertEqual(liquidation.fees[0].amount, "0.01")

    def test_binance_eth_staking_preserves_issued_and_redeemed_asset_legs(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        stake = connector.normalize(
            RawRecord(
                "binance", "eth-stake-1", OCCURRED_AT,
                {"_kind": "eth_stake", "purchaseId": "p1", "asset": "ETH", "amount": "1", "distributeAsset": "BETH", "distributeAmount": "1", "status": "SUCCESS"},
            )
        )
        redeem = connector.normalize(
            RawRecord(
                "binance", "eth-redeem-1", OCCURRED_AT,
                {"_kind": "eth_redeem", "redeemId": "r1", "asset": "WBETH", "amount": "0.9", "distributeAsset": "ETH", "distributeAmount": "1", "status": "SUCCESS"},
            )
        )
        reward = connector.normalize(
            RawRecord(
                "binance", "beth-reward-1", OCCURRED_AT,
                {"_kind": "beth_reward", "asset": "BETH", "amount": "0.002", "annualPercentageRate": "3.4", "status": "SUCCESS"},
            )
        )
        self.assertEqual((stake.event_type, stake.direction, stake.secondary_asset_symbol, stake.secondary_amount), ("STAKING_DEPOSIT", "-", "BETH", "1"))
        self.assertEqual((redeem.event_type, redeem.asset_symbol, redeem.secondary_asset_symbol, redeem.secondary_amount), ("STAKING_WITHDRAWAL", "WBETH", "ETH", "1"))
        self.assertEqual((reward.event_type, reward.asset_symbol, reward.amount), ("STAKING_REWARD", "BETH", "0.002"))

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

    def test_bitget_uta_fill_has_quote_leg_and_list_shaped_fees(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "uta-fill-1",
                OCCURRED_AT,
                {
                    "_kind": "uta_fill",
                    "_category": "SPOT",
                    "symbol": "BTCUSDT",
                    "side": "buy",
                    "execQty": "0.1",
                    "execValue": "5000",
                    "execId": "e1",
                    "orderId": "o1",
                    "feeDetail": [{"feeCoin": "USDT", "fee": "0.5"}],
                },
            )
        )
        self.assertEqual(event.event_type, "BUY")
        self.assertEqual(event.asset_symbol, "BTC")
        self.assertEqual(event.secondary_asset_symbol, "USDT")
        self.assertEqual(event.secondary_amount, "5000")
        self.assertEqual(event.fees[0].asset_symbol, "USDT")
        self.assertEqual(event.fees[0].amount, "0.5")
        self.assertEqual(event.trade_id, "e1")

    def test_bitget_uta_deposit_and_withdrawal_track_on_chain_hash(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        deposit = connector.normalize(
            RawRecord(
                "bitget",
                "uta-deposit-1",
                OCCURRED_AT,
                {"_kind": "uta_deposit", "coin": "usdt", "size": "100", "status": "success", "dest": "on_chain", "recordId": "0xhash", "orderId": "o1"},
            )
        )
        self.assertEqual(deposit.event_type, "DEPOSIT")
        self.assertEqual(deposit.asset_symbol, "USDT")
        self.assertEqual(deposit.tx_hash, "0xhash")

        internal_deposit = connector.normalize(
            RawRecord(
                "bitget",
                "uta-deposit-2",
                OCCURRED_AT,
                {"_kind": "uta_deposit", "coin": "usdt", "size": "1", "status": "success", "dest": "internal_transfer", "recordId": "order-id", "orderId": "o2"},
            )
        )
        self.assertIsNone(internal_deposit.tx_hash)

        withdrawal = connector.normalize(
            RawRecord(
                "bitget",
                "uta-withdrawal-1",
                OCCURRED_AT,
                {"_kind": "uta_withdrawal", "coin": "usdt", "size": "50", "status": "success", "dest": "on_chain", "recordId": "0xhash2", "orderId": "o3", "fee": "1"},
            )
        )
        self.assertEqual(withdrawal.event_type, "WITHDRAWAL")
        self.assertEqual(withdrawal.direction, "-")
        self.assertEqual(withdrawal.fees[0].amount, "1.0")
        self.assertEqual(withdrawal.tx_hash, "0xhash2")

    def test_bitget_deposit_and_withdrawal_capture_the_sender_address(self) -> None:
        # Bitget's deposit/withdrawal records carry both fromAddress (the
        # external sender/recipient) and toAddress (Bitget's own deposit
        # address, or the external withdrawal destination) — only toAddress
        # was ever read, so a deposit's actual source address never showed
        # up in Activity. Covers both the Classic (V2) and UTA (V3) shapes.
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")

        classic_deposit = connector.normalize(
            RawRecord(
                "bitget",
                "deposit-1",
                OCCURRED_AT,
                {"_kind": "deposit", "coin": "btc", "size": "0.5", "status": "success", "orderId": "d1", "fromAddress": "bc1qsender", "toAddress": "bc1qbitget"},
            )
        )
        self.assertEqual(classic_deposit.address_from, "bc1qsender")
        self.assertEqual(classic_deposit.address_to, "bc1qbitget")

        classic_withdrawal = connector.normalize(
            RawRecord(
                "bitget",
                "withdrawal-1",
                OCCURRED_AT,
                {"_kind": "withdrawal", "coin": "btc", "size": "0.1", "status": "success", "orderId": "w1", "fromAddress": "bc1qbitget", "toAddress": "bc1qrecipient"},
            )
        )
        self.assertEqual(classic_withdrawal.address_from, "bc1qbitget")
        self.assertEqual(classic_withdrawal.address_to, "bc1qrecipient")

        uta_deposit = connector.normalize(
            RawRecord(
                "bitget",
                "uta-deposit-3",
                OCCURRED_AT,
                {"_kind": "uta_deposit", "coin": "usdt", "size": "100", "status": "success", "dest": "on_chain", "recordId": "0xhash", "orderId": "o1", "fromAddress": "0xsender", "toAddress": "0xbitget"},
            )
        )
        self.assertEqual(uta_deposit.address_from, "0xsender")
        self.assertEqual(uta_deposit.address_to, "0xbitget")

    def test_bitget_uta_financial_record_maps_known_types_only(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        funding = connector.normalize(
            RawRecord(
                "bitget",
                "uta-financial-1",
                OCCURRED_AT,
                {"_kind": "uta_financial", "_category": "USDT-FUTURES", "type": "CONTRACT_MAIN_SETTLE_FEE_USER_OUT", "coin": "USDT", "amount": "-0.5", "id": "f1"},
            )
        )
        self.assertEqual(funding.event_type, "FUNDING_PAYMENT")
        self.assertEqual(funding.direction, "-")
        self.assertEqual(funding.amount, "0.5")

    def test_bitget_uta_transfer_types_are_never_mapped(self) -> None:
        # Regression guard: TRANSFER_IN/OUT within a unified account is
        # Bitget re-labeling collateral between its own product categories,
        # not a real external movement. Mapping both legs double-counted
        # the same money — this must stay unmapped.
        from app.connectors.bitget.live import _UTA_FINANCIAL_TYPE_MAP

        self.assertNotIn("TRANSFER_IN", _UTA_FINANCIAL_TYPE_MAP)
        self.assertNotIn("TRANSFER_OUT", _UTA_FINANCIAL_TYPE_MAP)

    def test_bitget_classic_pagination_consumes_all_pages(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        with patch.object(
            connector,
            "_get",
            side_effect=[
                [{"tradeId": "3"}, {"tradeId": "2"}],
                [{"tradeId": "1"}],
                [],
            ],
        ):
            records = list(connector._v2_paged("/api/v2/spot/trade/fills", {"limit": "100"}, id_key="tradeId"))
        self.assertEqual([record["tradeId"] for record in records], ["3", "2", "1"])

    def test_bitget_classic_futures_sync_uses_account_wide_fills_endpoint(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        called_paths: list[str] = []

        def no_records(path, *args, **kwargs):
            called_paths.append(path)
            return []

        connector._v2_windowed = no_records  # type: ignore[method-assign]
        connector._v2_numbered_windowed = no_records  # type: ignore[method-assign]
        connector._get_optional = lambda *args, **kwargs: []  # type: ignore[method-assign]

        self.assertEqual(list(connector._fetch_classic(OCCURRED_AT)), [])
        self.assertEqual(called_paths.count("/api/v2/mix/order/fills"), 3)
        self.assertNotIn("/api/v2/mix/order/fill-history", called_paths)

    def test_bitget_fixed_earn_and_deduction_keep_source_asset(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        interest = connector.normalize(
            RawRecord(
                "bitget",
                "earn-pay-interest-1",
                OCCURRED_AT,
                {"_kind": "earn_interest", "_periodType": "fixed", "coinName": "BGB", "amount": "0.25", "orderId": "e1"},
            )
        )
        deduction = connector.normalize(
            RawRecord(
                "bitget",
                "earn-deduction-1",
                OCCURRED_AT,
                {"_kind": "earn_deduction", "_periodType": "fixed", "coinName": "BGB", "amount": "0.1", "orderId": "e2"},
            )
        )
        self.assertEqual((interest.event_type, interest.asset_symbol, interest.direction), ("STAKING_REWARD", "BGB", "+"))
        self.assertEqual((deduction.event_type, deduction.asset_symbol, deduction.direction), ("STAKING_REWARD", "BGB", "-"))

    def test_bitget_additional_account_bills_preserve_rewards_and_convert_legs(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        airdrop = connector.normalize(
            RawRecord(
                "bitget",
                "spot-bill-airdrop",
                OCCURRED_AT,
                {"_kind": "spot_bill", "groupType": "financial", "businessType": "AIRDROP_REWARDS", "coin": "BGB", "size": "2", "billId": "a1"},
            )
        )
        convert_debit = connector.normalize(
            RawRecord(
                "bitget",
                "spot-bill-convert-out",
                OCCURRED_AT,
                {"_kind": "spot_bill", "groupType": "convert", "businessType": "SELL", "coin": "USDT", "size": "-100", "billId": "c1", "bizOrderId": "convert-1"},
            )
        )
        self.assertEqual((airdrop.event_type, airdrop.asset_symbol, airdrop.amount), ("AIRDROP", "BGB", "2"))
        self.assertEqual((convert_debit.event_type, convert_debit.direction, convert_debit.order_id), ("SELL", "-", "convert-1"))

    def test_bitget_classic_bills_preserve_fiat_cash_and_internal_wallet_transfers(self) -> None:
        from app.connectors.bitget.live import _is_additional_spot_bill

        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        self.assertTrue(_is_additional_spot_bill({"groupType": "fait", "businessType": "DEPOSIT"}))
        self.assertTrue(_is_additional_spot_bill({"groupType": "transfer", "businessType": "TRANSFER_OUT"}))
        fiat = connector.normalize(
            RawRecord("bitget", "spot-bill-fiat-1", OCCURRED_AT, {
                "_kind": "spot_bill", "groupType": "fiat", "businessType": "WITHDRAW", "coin": "EUR", "size": "-125", "billId": "f1",
            })
        )
        transfer = connector.normalize(
            RawRecord("bitget", "spot-bill-transfer-1", OCCURRED_AT, {
                "_kind": "spot_bill", "groupType": "transfer", "businessType": "TRANSFER_IN", "coin": "USDT", "size": "75", "billId": "t1",
            })
        )
        self.assertEqual((fiat.event_type, fiat.direction, fiat.asset_symbol, fiat.amount), ("WITHDRAWAL", "-", "EUR", "125"))
        self.assertEqual((transfer.event_type, transfer.direction, transfer.secondary_asset_symbol, transfer.secondary_amount), ("TRANSFER", "+", "USDT", "75"))

    def test_bitget_uta_futures_fill_close_with_pnl_creates_futures_pnl_event(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "uta-futures-fill-1",
                OCCURRED_AT,
                {
                    "_kind": "uta_futures_fill", "_category": "USDT-FUTURES", "symbol": "BTCUSDT",
                    "side": "sell", "tradeSide": "close", "execQty": "0.01", "execPnl": "-12.5",
                    "execId": "e1", "orderId": "o1", "feeDetail": [{"feeCoin": "USDT", "fee": "0.6"}],
                },
            )
        )
        self.assertEqual(event.event_type, "FUTURES_PNL")
        self.assertEqual(event.asset_symbol, "USDT")
        self.assertEqual(event.amount, "12.5")
        self.assertEqual(event.direction, "-")
        self.assertEqual(event.status, "COMPLETE")
        self.assertEqual(event.fees[0].amount, "0.6")

    def test_bitget_uta_futures_fill_open_is_informational_not_a_holdings_change(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "uta-futures-fill-2",
                OCCURRED_AT,
                {
                    "_kind": "uta_futures_fill", "_category": "USDT-FUTURES", "symbol": "BTCUSDT",
                    "side": "buy", "tradeSide": "open", "execQty": "0.01",
                    "execId": "e2", "orderId": "o2",
                },
            )
        )
        self.assertEqual(event.event_type, "FUTURES_OPEN")
        self.assertEqual(event.amount, "0")
        self.assertEqual(event.status, "REQUIRES_REVIEW")

    def test_bitget_uta_futures_fill_coin_margined_settles_in_base_coin(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "uta-futures-fill-3",
                OCCURRED_AT,
                {
                    "_kind": "uta_futures_fill", "_category": "COIN-FUTURES", "symbol": "BTCUSD",
                    "side": "sell", "tradeSide": "close", "execQty": "1", "execPnl": "0.001",
                    "execId": "e3", "orderId": "o3",
                },
            )
        )
        self.assertEqual(event.asset_symbol, "BTC")

    def test_bitget_classic_futures_fill_keeps_execution_pnl_and_fee(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "classic-futures-fill-USDT-FUTURES-t1",
                OCCURRED_AT,
                {
                    "_kind": "classic_futures_fill", "_productType": "USDT-FUTURES", "symbol": "BTCUSDT",
                    "tradeId": "t1", "orderId": "o1", "tradeSide": "close_long", "baseVolume": "0.01",
                    "marginCoin": "USDT", "profit": "-3.5",
                    "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.02"}],
                },
            )
        )
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("FUTURES_PNL", "-", "USDT", "3.5"))
        self.assertEqual((event.trade_id, event.order_id), ("t1", "o1"))
        self.assertEqual((event.fees[0].asset_symbol, event.fees[0].amount), ("USDT", "0.02"))

    def test_bitget_classic_futures_execution_bills_are_not_double_counted(self) -> None:
        from app.connectors.bitget.live import _is_futures_execution_bill

        self.assertTrue(_is_futures_execution_bill({"businessType": "close_long"}))
        self.assertFalse(_is_futures_execution_bill({"businessType": "contract_settle_fee"}))

    def test_bitget_uta_convert_has_both_legs(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        convert = connector.normalize(
            RawRecord(
                "bitget",
                "uta-convert-1",
                OCCURRED_AT,
                {"_kind": "uta_convert", "fromCoin": "USDT", "fromCoinSize": "100", "toCoin": "ETH", "toCoinSize": "0.03"},
            )
        )
        self.assertEqual(convert.event_type, "BUY")
        self.assertEqual(convert.asset_symbol, "ETH")
        self.assertEqual(convert.amount, "0.03")
        self.assertEqual(convert.secondary_asset_symbol, "USDT")
        self.assertEqual(convert.secondary_amount, "100")

    def test_bitget_uta_crypto_loan_keeps_borrow_collateral_repayment_and_interest_separate(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        borrow = connector.normalize(
            RawRecord(
                "bitget", "uta-loan-borrow-1", OCCURRED_AT,
                {
                    "_kind": "uta_loan_borrow", "orderId": "loan-1", "loanCoin": "USDT",
                    "initLoanAmount": "100", "pledgeCoin": "ETH", "initPledgeAmount": "0.05", "daily": "FLEXIBLE",
                },
            )
        )
        self.assertEqual((borrow.event_type, borrow.direction, borrow.asset_symbol, borrow.amount), ("MARGIN_BORROW", "+", "USDT", "100"))
        self.assertEqual((borrow.secondary_asset_symbol, borrow.secondary_amount), ("ETH", "0.05"))

        principal = connector.normalize(
            RawRecord(
                "bitget", "uta-loan-repay-1-principal", OCCURRED_AT,
                {
                    "_kind": "uta_loan_repay_principal", "orderId": "loan-1", "loanCoin": "USDT",
                    "repayLoanAmount": "80", "pledgeCoin": "ETH", "repayUnlockAmount": "0.04",
                },
            )
        )
        interest = connector.normalize(
            RawRecord(
                "bitget", "uta-loan-repay-1-interest", OCCURRED_AT,
                {"_kind": "uta_loan_repay_interest", "orderId": "loan-1", "loanCoin": "USDT", "payInterest": "0.15"},
            )
        )
        self.assertEqual((principal.event_type, principal.direction, principal.secondary_asset_symbol, principal.secondary_amount), ("MARGIN_REPAY", "-", "ETH", "0.04"))
        self.assertEqual((interest.event_type, interest.direction, interest.amount), ("MARGIN_INTEREST", "-", "0.15"))

    def test_bitget_uta_crypto_loan_liquidation_and_collateral_adjustment_are_typed(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        liquidation = connector.normalize(
            RawRecord(
                "bitget", "uta-loan-reduce-1", OCCURRED_AT,
                {
                    "_kind": "uta_loan_reduce", "orderId": "loan-1", "pledgeCoin": "BGB",
                    "pledgeAmount": "20", "reduceFee": "0.1", "status": "complete",
                },
            )
        )
        adjustment = connector.normalize(
            RawRecord(
                "bitget", "uta-loan-pledge-1", OCCURRED_AT,
                {"_kind": "uta_loan_pledge_adjustment", "orderId": "loan-1", "pledgeCoin": "BGB", "reviseAmount": "4", "reviseSide": "down"},
            )
        )
        self.assertEqual((liquidation.event_type, liquidation.direction, liquidation.amount), ("LIQUIDATION", "-", "20"))
        self.assertEqual(liquidation.fees[0].amount, "0.1")
        self.assertEqual((adjustment.event_type, adjustment.direction), ("LENDING_DEPOSIT", "-"))

    def test_bitget_uta_numbered_history_paginates_to_a_short_page(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        first_page = [{"orderId": str(index)} for index in range(100)]
        with patch.object(connector, "_get", side_effect=[first_page, [{"orderId": "last"}]]) as request:
            records = list(connector._v3_numbered_windowed("/api/v3/loan/borrow-history", {}, 0, 1))
        self.assertEqual(len(records), 101)
        self.assertEqual(request.call_args_list[1].args[1]["pageNum"], "2")

    def test_bitget_classic_crypto_loan_paginates_with_page_no(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")
        first_page = [{"orderId": str(index)} for index in range(100)]
        with patch.object(connector, "_get", side_effect=[first_page, [{"orderId": "last"}]]) as request:
            records = list(connector._v2_numbered_windowed("/api/v2/earn/loan/borrow-history", {}, 0, 1))
        self.assertEqual(len(records), 101)
        self.assertEqual(request.call_args_list[1].args[1]["pageNo"], "2")

    def test_bitget_mode_detection_switches_to_uta_on_40085(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")

        def fake_get(path, params=None):
            if path == "/api/v2/spot/account/info":
                from app.connectors.bitget.live import BitgetApiError

                raise BitgetApiError("40085", "You are in Unified Account mode, and the Classic Account API is not supported at this time")
            raise AssertionError(f"unexpected path {path}")

        connector._get = fake_get  # type: ignore[method-assign]
        connector._detect_mode()
        self.assertEqual(connector.mode, "uta")

    def test_bitget_mode_detection_reraises_other_errors(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "pass", "Bitget")

        def fake_get(path, params=None):
            from app.connectors.bitget.live import BitgetApiError

            raise BitgetApiError("40001", "invalid sign")

        connector._get = fake_get  # type: ignore[method-assign]
        with self.assertRaises(Exception):
            connector._detect_mode()
        self.assertIsNone(connector.mode)

    def test_binance_convert_and_fiat_events(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        convert = connector.normalize(
            RawRecord("binance", "convert-1", OCCURRED_AT, {"_kind": "convert", "fromAsset": "USDT", "fromAmount": "20", "toAsset": "BNB", "toAmount": "0.06", "orderId": 1})
        )
        self.assertEqual(convert.event_type, "BUY")
        self.assertEqual(convert.asset_symbol, "BNB")
        self.assertEqual(convert.secondary_asset_symbol, "USDT")
        self.assertEqual(convert.secondary_amount, "20")

        fiat_buy = connector.normalize(
            RawRecord(
                "binance",
                "fiat-buy-1",
                OCCURRED_AT,
                {"_kind": "fiat_buy", "cryptoCurrency": "BTC", "obtainAmount": "0.01", "fiatCurrency": "EUR", "sourceAmount": "500", "totalFee": "5", "orderNo": "n1"},
            )
        )
        self.assertEqual(fiat_buy.event_type, "BUY")
        self.assertEqual(fiat_buy.asset_symbol, "BTC")
        self.assertEqual(fiat_buy.amount, "0.01")
        self.assertEqual(fiat_buy.secondary_asset_symbol, "EUR")
        self.assertEqual(fiat_buy.fees[0].amount, "5")

        fiat_sell = connector.normalize(
            RawRecord(
                "binance",
                "fiat-sell-1",
                OCCURRED_AT,
                {"_kind": "fiat_sell", "cryptoCurrency": "BTC", "sourceAmount": "0.01", "fiatCurrency": "EUR", "obtainAmount": "500", "orderNo": "n2"},
            )
        )
        self.assertEqual(fiat_sell.event_type, "SELL")
        self.assertEqual(fiat_sell.direction, "-")
        self.assertEqual(fiat_sell.amount, "0.01")
        self.assertEqual(fiat_sell.secondary_amount, "500")

    def test_binance_discover_symbols_from_balances(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")

        def fake_signed_get(base, path, params=None):
            self.assertEqual(path, "/api/v3/account")
            return {"balances": [{"asset": "SOL", "free": "1.5", "locked": "0"}, {"asset": "USDT", "free": "0", "locked": "0"}]}

        connector._signed_get = fake_signed_get  # type: ignore[method-assign]
        symbols = connector._discover_symbols()
        self.assertIn("SOLUSDT", symbols)
        self.assertIn("SOLUSDC", symbols)
        self.assertTrue(all(not s.startswith("USDT") for s in symbols), "USDT has zero balance, shouldn't be a base asset")

    def test_binance_balance_reconciliation_includes_non_spot_wallets(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")

        def get(base, path, params=None):
            if path == "/api/v3/account":
                return {"balances": [{"asset": "BTC", "free": "1", "locked": "0.25"}]}
            if path == "/sapi/v1/margin/account":
                return {"userAssets": [{"asset": "USDT", "netAsset": "50"}]}
            if path == "/sapi/v1/simple-earn/flexible/position":
                return {"total": 1, "rows": [{"asset": "SOL", "totalAmount": "3"}]}
            if path == "/sapi/v1/simple-earn/locked/position":
                return {"total": 1, "rows": [{"asset": "SOL", "amount": "2"}]}
            if path == "/fapi/v2/balance":
                return [{"asset": "USDT", "balance": "5"}]
            if path == "/dapi/v1/balance":
                return [{"asset": "BTC", "balance": "0.5"}]
            if path == "/eapi/v1/marginAccount":
                return {"asset": []}
            if path == "/sapi/v1/bfusd/account":
                return {"bfusdAmount": "2"}
            if path == "/sapi/v1/rwusd/account":
                return {"rwusdAmount": "3"}
            self.fail(f"unexpected GET endpoint {path}")

        connector._signed_get = get  # type: ignore[method-assign]
        connector._signed_post_optional = lambda *args, **kwargs: [{"asset": "USDT", "free": "10", "locked": "1", "freeze": "0", "withdrawing": "0"}]  # type: ignore[method-assign]
        balances = {balance.asset_symbol: balance.amount for balance in connector.fetch_balances()}
        self.assertEqual(balances, {"BFUSD": "2", "BTC": "1.75", "RWUSD": "3", "SOL": "5", "USDT": "66"})

    def test_bitget_classic_balance_reconciliation_includes_futures_collateral(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        connector.mode = "classic"

        def get(path, params=None):
            if path == "/api/v2/spot/account/assets":
                return [{"coin": "BTC", "available": "1", "frozen": "0.1"}]
            if path == "/api/v2/mix/account/accounts":
                return [{"marginCoin": "USDT", "available": "5", "locked": "2"}]
            if path == "/api/v2/margin/crossed/account/assets":
                return [{"coin": "ETH", "available": "3", "frozen": "0.5", "borrow": "1", "interest": "0.1", "net": "2.4"}]
            if path == "/api/v2/margin/isolated/account/assets":
                return [{"coin": "BTC", "symbol": "BTCUSDT", "available": "0.3", "frozen": "0", "borrow": "0", "interest": "0", "net": "0.3"}]
            if path == "/api/v2/earn/loan/debts":
                return {"pledgeInfos": []}
            self.fail(f"unexpected Bitget endpoint {path}")

        connector._get = get  # type: ignore[method-assign]
        balances = {balance.asset_symbol: balance.amount for balance in connector.fetch_balances()}
        self.assertEqual(balances, {"BTC": "1.4", "ETH": "2.4", "USDT": "21"})

    def test_bitget_margin_financial_ledger_preserves_cross_and_isolated_activity(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        event = connector.normalize(
            RawRecord(
                "bitget",
                "margin-financial-isolated-BTCUSDT-1",
                OCCURRED_AT,
                {
                    "_kind": "margin_financial",
                    "_margin_mode": "isolated",
                    "_margin_symbol": "BTCUSDT",
                    "marginId": "1",
                    "marginType": "deal_in",
                    "coin": "BTC",
                    "amount": "0.02",
                    "fee": "0.00002",
                },
            )
        )
        self.assertEqual((event.event_type, event.event_subtype, event.direction, event.asset_symbol, event.amount), ("BUY", "margin:isolated:deal_in", "+", "BTC", "0.02"))
        self.assertEqual(event.fees[0].amount, "0.00002")

        self.assertFalse(_is_additional_margin_financial({"marginType": "borrow"}))

    def test_bitget_isolated_margin_borrow_and_repay_keep_the_pair_context(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        borrow = connector.normalize(
            RawRecord(
                "bitget", "margin-borrow-isolated-BTCUSDT-1", OCCURRED_AT,
                {"_kind": "margin_borrow", "_margin_mode": "isolated", "_margin_symbol": "BTCUSDT", "loanId": "1", "coin": "USDT", "borrowAmount": "25"},
            )
        )
        repay = connector.normalize(
            RawRecord(
                "bitget", "margin-repay-isolated-BTCUSDT-1", OCCURRED_AT,
                {"_kind": "margin_repay", "_margin_mode": "isolated", "_margin_symbol": "BTCUSDT", "repayId": "1", "coin": "USDT", "repayAmount": "10"},
            )
        )
        self.assertEqual((borrow.event_type, borrow.event_subtype, borrow.amount), ("MARGIN_BORROW", "isolated_margin", "25"))
        self.assertEqual((repay.event_type, repay.event_subtype, repay.amount), ("MARGIN_REPAY", "isolated_margin", "10"))

    def test_binance_coin_m_futures_income_preserves_its_settlement_mode(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "coinm-futures-income-1", OCCURRED_AT,
                {"_kind": "futures_income", "_futures_mode": "coin_m", "incomeType": "FUNDING_FEE", "asset": "BTC", "income": "0.0001", "tranId": 1},
            )
        )
        self.assertEqual((event.event_type, event.event_subtype, event.asset_symbol, event.amount), ("FUNDING_PAYMENT", "futures:coin_m:FUNDING_FEE", "BTC", "0.0001"))

    def test_binance_futures_fill_keeps_contract_context_without_changing_holdings(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "usdt_m-futures-trade-BTCUSDT-1", OCCURRED_AT,
                {
                    "_kind": "futures_trade", "_futures_mode": "usdt_m", "id": 1, "orderId": 2,
                    "side": "BUY", "positionSide": "LONG", "symbol": "BTCUSDT", "price": "50000",
                    "qty": "0.01", "quoteQty": "500", "marginAsset": "USDT", "commission": "0.2",
                    "commissionAsset": "USDT", "realizedPnl": "0",
                },
            )
        )
        self.assertEqual((event.event_type, event.status, event.asset_symbol, event.amount), ("FUTURES_OPEN", "COMPLETE", "USDT", "500"))
        self.assertEqual((event.secondary_asset_symbol, event.secondary_amount), ("USDT", "500"))
        self.assertIn("commission 0.2 USDT", event.notes or "")

    def test_binance_options_events_preserve_derivative_and_settlement_evidence(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        trade = connector.normalize(
            RawRecord(
                "binance", "options-trade-1", OCCURRED_AT,
                {"_kind": "options_trade", "id": 1, "tradeId": 2, "orderId": 3, "symbol": "BTC-240628-50000-C", "side": "BUY", "optionSide": "CALL", "price": "100", "quantity": "0.1", "fee": "-0.01", "quoteAsset": "USDT", "realizedProfit": "0"},
            )
        )
        exercise = connector.normalize(
            RawRecord(
                "binance", "options-exercise-1", OCCURRED_AT,
                {"_kind": "options_exercise", "id": "1", "currency": "USDT", "symbol": "BTC-240628-50000-C", "amount": "25", "fee": "0.1", "exercisePrice": "50000", "optionSide": "CALL", "positionSide": "LONG"},
            )
        )
        bill = connector.normalize(
            RawRecord(
                "binance", "options-bill-1", OCCURRED_AT,
                {"_kind": "options_bill", "id": 1, "asset": "USDT", "amount": "-0.1", "type": "TRANSACTION_FEE"},
            )
        )
        self.assertEqual((trade.event_type, trade.asset_symbol, trade.amount, trade.secondary_amount), ("OPTION_TRADE", "USDT", "10.0", "10.0"))
        self.assertEqual((exercise.event_type, exercise.direction, exercise.amount, exercise.fees[0].amount), ("OPTION_EXERCISE", "+", "25", "0.1"))
        self.assertEqual((bill.event_type, bill.direction, bill.amount), ("EXCHANGE_FEE", "-", "0.1"))

    def test_binance_leveraged_token_records_keep_usdt_trade_legs_and_fees(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        subscribe = connector.normalize(
            RawRecord(
                "binance", "blvt-subscribe-1", OCCURRED_AT,
                {"_kind": "blvt_subscribe", "id": 1, "tokenName": "BTCUP", "amount": "2", "totalCharge": "100", "fee": "0.1", "nav": "50"},
            )
        )
        redeem = connector.normalize(
            RawRecord(
                "binance", "blvt-redeem-2", OCCURRED_AT,
                {"_kind": "blvt_redeem", "id": 2, "tokenName": "BTCUP", "amount": "2", "netProceed": "110", "fee": "0.1", "nav": "55"},
            )
        )
        self.assertEqual((subscribe.event_type, subscribe.direction, subscribe.asset_symbol, subscribe.secondary_asset_symbol, subscribe.secondary_amount), ("BUY", "+", "BTCUP", "USDT", "100"))
        self.assertEqual(subscribe.fees[0].amount, "0.1")
        self.assertEqual((redeem.event_type, redeem.direction, redeem.asset_symbol, redeem.secondary_asset_symbol, redeem.secondary_amount), ("SELL", "-", "BTCUP", "USDT", "110"))

    def test_binance_wbeth_wrap_uses_the_documented_two_asset_conversion(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "wbeth-wrap-1", OCCURRED_AT,
                {"_kind": "wbeth_wrap", "fromAsset": "BETH", "fromAmount": "1", "toAsset": "WBETH", "toAmount": "0.95", "exchangeRate": "0.95", "status": "SUCCESS"},
            )
        )
        self.assertEqual((event.event_type, event.direction, event.asset_symbol, event.amount), ("SWAP", "-", "BETH", "1"))
        self.assertEqual((event.secondary_asset_symbol, event.secondary_amount), ("WBETH", "0.95"))

    def test_binance_fiat_and_pay_records_are_auditable(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        fiat = connector.normalize(
            RawRecord(
                "binance", "fiat-deposit-1", OCCURRED_AT,
                {"_kind": "fiat_deposit", "orderNo": "1", "fiatCurrency": "EUR", "amount": "100", "totalFee": "1", "status": "Successful", "method": "Bank transfer"},
            )
        )
        payment = connector.normalize(
            RawRecord(
                "binance", "pay-1", OCCURRED_AT,
                {"_kind": "pay", "transactionId": "1", "orderType": "PAY", "currency": "USDT", "amount": "25", "receiverInfo": {"name": "Merchant"}},
            )
        )
        unknown_pay = connector.normalize(
            RawRecord(
                "binance", "pay-2", OCCURRED_AT,
                {"_kind": "pay", "transactionId": "2", "orderType": "C2C", "currency": "BNB", "amount": "1"},
            )
        )
        self.assertEqual((fiat.event_type, fiat.direction, fiat.asset_symbol, fiat.amount, fiat.fees[0].amount), ("DEPOSIT", "+", "EUR", "100", "1"))
        self.assertEqual((payment.event_type, payment.direction, payment.address_to), ("PAYMENT", "-", "Merchant"))
        self.assertEqual((unknown_pay.event_type, unknown_pay.status), ("UNKNOWN", "REQUIRES_REVIEW"))

    def test_binance_spot_rebates_distinguish_cashback_and_referral_credits(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        commission_rebate = connector.normalize(
            RawRecord(
                "binance", "spot-rebate-1-BNB-0.01-1", OCCURRED_AT,
                {"_kind": "spot_rebate", "type": 1, "asset": "BNB", "amount": "0.01", "updateTime": 1},
            )
        )
        referral_kickback = connector.normalize(
            RawRecord(
                "binance", "spot-rebate-2-USDT-1-2", OCCURRED_AT,
                {"_kind": "spot_rebate", "type": 2, "asset": "USDT", "amount": "1", "updateTime": 2},
            )
        )
        self.assertEqual(
            (commission_rebate.event_type, commission_rebate.direction, commission_rebate.asset_symbol, commission_rebate.amount),
            ("CASHBACK", "+", "BNB", "0.01"),
        )
        self.assertEqual(
            (referral_kickback.event_type, referral_kickback.direction, referral_kickback.asset_symbol, referral_kickback.amount),
            ("REFERRAL_REWARD", "+", "USDT", "1"),
        )

    def test_binance_bfusd_and_rwusd_keep_yield_asset_pairs_and_rewards(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        subscription = connector.normalize(
            RawRecord(
                "binance", "bfusd-yield-subscribe-1", OCCURRED_AT,
                {"_kind": "yield_subscribe", "_yield_product": "bfusd", "asset": "USDT", "amount": "100", "receiveAsset": "BFUSD", "receiveAmount": "100", "status": "SUCCESS"},
            )
        )
        redemption = connector.normalize(
            RawRecord(
                "binance", "rwusd-yield-redeem-1", OCCURRED_AT,
                {"_kind": "yield_redeem", "_yield_product": "rwusd", "asset": "RWUSD", "amount": "20", "receiveAsset": "USDT", "receiveAmount": "20", "status": "SUCCESS"},
            )
        )
        reward = connector.normalize(
            RawRecord(
                "binance", "bfusd-yield-reward-1", OCCURRED_AT,
                {"_kind": "yield_reward", "_yield_product": "bfusd", "rewardAsset": "USDT", "rewardsAmount": "0.1", "BFUSDPosition": "100", "annualPercentageRate": "0.05"},
            )
        )
        self.assertEqual((subscription.event_type, subscription.asset_symbol, subscription.secondary_asset_symbol, subscription.secondary_amount), ("STAKING_DEPOSIT", "USDT", "BFUSD", "100"))
        self.assertEqual((redemption.event_type, redemption.asset_symbol, redemption.secondary_asset_symbol, redemption.secondary_amount), ("STAKING_WITHDRAWAL", "RWUSD", "USDT", "20"))
        self.assertEqual((reward.event_type, reward.asset_symbol, reward.amount), ("YIELD", "USDT", "0.1"))

    def test_binance_cloud_mining_payments_and_refunds_keep_their_direction(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        payment = connector.normalize(
            RawRecord(
                "binance", "cloud-mining-1", OCCURRED_AT,
                {"_kind": "cloud_mining", "type": 248, "asset": "USDT", "amount": "25", "tranId": 1},
            )
        )
        refund = connector.normalize(
            RawRecord(
                "binance", "cloud-mining-2", OCCURRED_AT,
                {"_kind": "cloud_mining", "type": 249, "asset": "USDT", "amount": "5", "tranId": 2},
            )
        )
        self.assertEqual((payment.event_type, payment.direction, payment.asset_symbol, payment.amount), ("PAYMENT", "-", "USDT", "25"))
        self.assertEqual((refund.event_type, refund.direction, refund.asset_symbol, refund.amount), ("RECEIVE", "+", "USDT", "5"))

    def test_binance_universal_transfer_is_visible_but_balance_neutral(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        event = connector.normalize(
            RawRecord(
                "binance", "universal-transfer-1", OCCURRED_AT,
                {"_kind": "universal_transfer", "tranId": 1, "type": "MAIN_UMFUTURE", "status": "CONFIRMED", "asset": "USDT", "amount": "100"},
            )
        )
        self.assertEqual((event.event_type, event.direction, event.status), ("TRANSFER", "-", "COMPLETE"))
        self.assertEqual((event.asset_symbol, event.amount, event.secondary_asset_symbol, event.secondary_amount), ("USDT", "100", "USDT", "100"))

    def test_binance_pool_payouts_keep_mining_context_and_safe_classifications(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        earning = connector.normalize(
            RawRecord(
                "binance", "mining-earning-sha256-main-1", OCCURRED_AT,
                {"_kind": "mining_earning", "_mining_algo": "sha256", "_mining_account": "main", "time": 1, "coinName": "BTC", "type": 0, "profitAmount": "0.0001", "status": 2, "dayHashRate": 100},
            )
        )
        rebate = connector.normalize(
            RawRecord(
                "binance", "mining-bonus-sha256-main-1", OCCURRED_AT,
                {"_kind": "mining_bonus", "_mining_algo": "sha256", "_mining_account": "main", "time": 1, "coinName": "BTC", "type": 3, "profitAmount": "0.00001", "status": 2},
            )
        )
        transferred = connector.normalize(
            RawRecord(
                "binance", "mining-earning-sha256-main-2", OCCURRED_AT,
                {"_kind": "mining_earning", "_mining_algo": "sha256", "_mining_account": "main", "time": 2, "coinName": "BTC", "type": 8, "profitAmount": "0.0001", "status": 2},
            )
        )
        self.assertEqual((earning.event_type, earning.status, earning.asset_symbol, earning.amount), ("MINING_REWARD", "COMPLETE", "BTC", "0.0001"))
        self.assertIn("main", earning.notes or "")
        self.assertEqual((rebate.event_type, rebate.status, rebate.asset_symbol, rebate.amount), ("CASHBACK", "COMPLETE", "BTC", "0.00001"))
        self.assertEqual((transferred.event_type, transferred.status), ("UNKNOWN", "REQUIRES_REVIEW"))

    def test_binance_pool_history_discovers_algorithms_accounts_and_payouts(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")
        connector._public_get_optional_object = lambda path, params=None: {"data": [{"algoName": "sha256"}]}  # type: ignore[method-assign]

        def get(base, path, params=None):
            if path == "/sapi/v1/mining/payment/uid":
                return {"data": {"accountProfits": [], "totalNum": 0}}
            if path == "/sapi/v1/mining/statistics/user/list":
                self.assertEqual(params["algo"], "sha256")
                return {"data": [{"userName": "main"}]}
            if path == "/sapi/v1/mining/payment/list":
                self.assertEqual((params["algo"], params["userName"]), ("sha256", "main"))
                return {"data": {"accountProfits": [{"time": 1, "coinName": "BTC", "profitAmount": "0.1", "status": 2}], "totalNum": 1}}
            if path == "/sapi/v1/mining/payment/other":
                return {"data": {"otherProfits": [], "totalNum": 0}}
            self.fail(f"unexpected mining endpoint {path}")

        connector._signed_get = get  # type: ignore[method-assign]
        records = list(connector._mining_history(datetime(2026, 8, 1, tzinfo=timezone.utc)))
        self.assertEqual(records, [("mining_earning", {"time": 1, "coinName": "BTC", "profitAmount": "0.1", "status": 2, "_mining_algo": "sha256", "_mining_account": "main"})])

    def test_bitget_uta_financials_map_interest_copy_share_and_keep_new_types_reviewable(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        interest = connector.normalize(
            RawRecord(
                "bitget",
                "uta-financial-interest-1",
                OCCURRED_AT,
                {"_kind": "uta_financial", "_category": "MARGIN", "id": "1", "type": "INTEREST_SETTLEMENT_OUT", "coin": "USDT", "amount": "-0.12"},
            )
        )
        self.assertEqual((interest.event_type, interest.direction, interest.amount), ("MARGIN_INTEREST", "-", "0.12"))

        copy_share = connector.normalize(
            RawRecord(
                "bitget",
                "uta-financial-copy-1",
                OCCURRED_AT,
                {"_kind": "uta_financial", "_category": "OTHER", "id": "2", "type": "TRACE_SHARE_BENEFIT_USER_OUT", "coin": "USDT", "amount": "-2"},
            )
        )
        self.assertEqual((copy_share.event_type, copy_share.direction), ("FEE", "-"))
        self.assertTrue(_is_additional_uta_financial({"type": "NEW_BITGET_PRODUCT"}))
        self.assertFalse(_is_additional_uta_financial({"type": "ORDER_DEALT_IN"}))

    def test_bitget_copy_trading_transfer_is_an_internal_allocation_not_an_external_withdrawal(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        allocation = connector.normalize(
            RawRecord(
                "bitget", "uta-copy-transfer-1", OCCURRED_AT,
                {"_kind": "uta_copy_transfer", "transferId": "1", "fromType": "uta", "toType": "lead", "coin": "USDT", "amount": "100", "status": "Successful"},
            )
        )
        release = connector.normalize(
            RawRecord(
                "bitget", "uta-copy-transfer-2", OCCURRED_AT,
                {"_kind": "uta_copy_transfer", "transferId": "2", "fromType": "lead", "toType": "funding,uta", "coin": "USDT", "amount": "30", "status": "Successful"},
            )
        )
        self.assertEqual((allocation.event_type, allocation.direction, allocation.amount), ("LENDING_DEPOSIT", "-", "100"))
        self.assertEqual((release.event_type, release.direction, release.amount), ("LENDING_WITHDRAWAL", "+", "30"))

    def test_bitget_internal_wallet_transfers_remain_visible_and_balance_neutral(self) -> None:
        connector = BitgetLiveConnector("key", "secret", "passphrase", "Bitget")
        uta = connector.normalize(
            RawRecord(
                "bitget", "uta-internal-transfer-spot-1", OCCURRED_AT,
                {"_kind": "uta_internal_transfer", "_category": "SPOT", "id": "1", "type": "TRANSFER_OUT", "coin": "USDT", "amount": "100"},
            )
        )
        classic_margin = connector.normalize(
            RawRecord(
                "bitget", "margin-internal-transfer-crossed-1", OCCURRED_AT,
                {"_kind": "margin_internal_transfer", "_margin_mode": "crossed", "marginId": "1", "marginType": "transfer_in", "coin": "BTC", "amount": "0.1"},
            )
        )
        future = connector.normalize(
            RawRecord(
                "bitget", "futures-bill-1", OCCURRED_AT,
                {"_kind": "futures_bill", "_productType": "USDT-FUTURES", "billId": "1", "businessType": "trans_from_exchange", "coin": "USDT", "amount": "-5"},
            )
        )
        self.assertEqual((uta.event_type, uta.direction, uta.asset_symbol, uta.secondary_asset_symbol, uta.secondary_amount), ("TRANSFER", "-", "USDT", "USDT", "100"))
        self.assertEqual((classic_margin.event_type, classic_margin.direction, classic_margin.asset_symbol, classic_margin.secondary_asset_symbol, classic_margin.secondary_amount), ("TRANSFER", "+", "BTC", "BTC", "0.1"))
        self.assertEqual((future.event_type, future.asset_symbol, future.amount, future.secondary_asset_symbol, future.secondary_amount), ("TRANSFER", "USDT", "5", "USDT", "5"))

    def test_binance_windowed_history_covers_a_deep_backfill_not_just_7_days(self) -> None:
        # Regression guard: Binance defaults to the *last 7 days* when a
        # history call omits startTime/endTime — fetch() must always pass
        # explicit windowed bounds so a backfill (since=None) reaches back
        # years, not silently truncate to the last week.
        connector = BinanceLiveConnector("key", "secret", "Binance")
        calls: list[dict] = []

        def fake_signed_get(base, path, params=None):
            calls.append(dict(params or {}))
            self.assertIn("startTime", params)
            self.assertIn("endTime", params)
            return []

        connector._signed_get = fake_signed_get  # type: ignore[method-assign]
        list(connector._windowed_history("https://api.binance.com", "/sapi/v1/capital/withdraw/history", None))

        self.assertGreater(len(calls), 1, "a 3-year backfill must span more than one 90-day window")
        earliest_start = min(int(c["startTime"]) for c in calls)
        latest_end = max(int(c["endTime"]) for c in calls)
        span_days = (latest_end - earliest_start) / (24 * 3600 * 1000)
        self.assertGreater(span_days, 300, "backfill window collapsed back to a short default span")

    def test_binance_windowed_history_first_window_is_mandatory(self) -> None:
        connector = BinanceLiveConnector("key", "secret", "Binance")

        def failing_signed_get(base, path, params=None):
            raise ConnectorUnavailable("bad key")

        connector._signed_get = failing_signed_get  # type: ignore[method-assign]
        with self.assertRaises(ConnectorUnavailable):
            list(connector._windowed_history("https://api.binance.com", "/sapi/v1/capital/withdraw/history", datetime(2026, 8, 1, tzinfo=timezone.utc)))

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
