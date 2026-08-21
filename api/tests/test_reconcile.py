from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import Balance, ConnectorUnavailable
from app.core.ledger.reconcile import compute_account_holdings, reconcile_account, store_account_balances
from app.db.models import Account, AccountBalance, Asset, Base, Event, Fee

OCCURRED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _FakeConnector:
    def __init__(self, balances):
        self._balances = balances

    def fetch_balances(self):
        return self._balances


class _FailingConnector:
    def fetch_balances(self):
        raise ConnectorUnavailable("could not reach exchange")


class _NoBalanceConnector:
    def fetch(self, since=None):
        return []

    def normalize(self, raw):
        raise NotImplementedError


class ReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _account(self) -> Account:
        account = Account(name="Test", kind="exchange", connector_type="binance_live", status="connected")
        self.session.add(account)
        self.session.flush()
        return account

    def _add_event(self, account_id: int, asset: Asset, amount: str, direction: str, fee_amount: str | None = None) -> None:
        event = Event(
            external_id=f"evt-{asset.symbol}-{amount}-{direction}-{fee_amount}",
            account_id=account_id,
            event_type="BUY" if direction == "+" else "SELL",
            direction=direction,
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=asset.id,
            primary_amount=amount,
            source_label="Test",
            provenance="manual",
            normalizer_version="test",
        )
        self.session.add(event)
        self.session.flush()
        if fee_amount is not None:
            self.session.add(Fee(event_id=event.id, fee_type="TRADING_FEE", fee_asset_id=asset.id, fee_amount=fee_amount))
        self.session.flush()

    def _add_trade(self, account_id: int, base: Asset, base_amount: str, direction: str, quote: Asset, quote_amount: str) -> None:
        event = Event(
            external_id=f"trade-{base.symbol}-{base_amount}-{direction}-{quote.symbol}",
            account_id=account_id,
            event_type="BUY" if direction == "+" else "SELL",
            direction=direction,
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=base.id,
            primary_amount=base_amount,
            secondary_asset_id=quote.id,
            secondary_amount=quote_amount,
            source_label="Test",
            provenance="manual",
            normalizer_version="test",
        )
        self.session.add(event)
        self.session.flush()

    def test_trade_secondary_leg_reduces_quote_asset_holdings(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN")
        usdt = Asset(symbol="USDT", name="Tether", asset_type="STABLECOIN")
        self.session.add_all([btc, usdt])
        self.session.flush()
        # Deposited 5000 USDT, then spent all of it buying 0.1 BTC.
        self._add_event(account.id, usdt, "5000", "+")
        self._add_trade(account.id, btc, "0.1", "+", usdt, "5000")

        holdings = compute_account_holdings(self.session, account.id)
        self.assertEqual(holdings[("BTC", None, None)], Decimal("0.1"))
        self.assertEqual(holdings[("USDT", None, None)], Decimal("0"))

    def test_sell_trade_secondary_leg_increases_quote_asset_holdings(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN")
        usdt = Asset(symbol="USDT", name="Tether", asset_type="STABLECOIN")
        self.session.add_all([btc, usdt])
        self.session.flush()
        self._add_event(account.id, btc, "0.1", "+")
        self._add_trade(account.id, btc, "0.1", "-", usdt, "6000")

        holdings = compute_account_holdings(self.session, account.id)
        self.assertEqual(holdings[("BTC", None, None)], Decimal("0"))
        self.assertEqual(holdings[("USDT", None, None)], Decimal("6000"))

    def test_exchange_balance_matches_ledger_despite_no_network_on_the_live_side(self) -> None:
        # get_or_create_asset defaults BTC's ledger Asset row to
        # network="Bitcoin" even for an exchange-sourced event (no network
        # given) — an exchange's live Balance never carries a network either,
        # so the two sides must still resolve to the same key or every
        # native-coin exchange balance would show a phantom mismatch.
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._add_event(account.id, btc, "1.0", "+")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "1.0")])):
            result = reconcile_account(self.session, account)

        self.assertEqual(len(result.assets), 1)
        self.assertTrue(result.assets[0].matches)
        self.assertEqual(result.assets[0].asset_network, "Bitcoin")

    def test_matching_balance_is_not_flagged(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._add_event(account.id, btc, "1.0", "+")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "1.0")])):
            result = reconcile_account(self.session, account)

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.assets), 1)
        self.assertTrue(result.assets[0].matches)

    def test_mismatch_beyond_tolerance_is_flagged(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._add_event(account.id, btc, "1.0", "+")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "0.5")])):
            result = reconcile_account(self.session, account)

        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.assets), 1)
        asset = result.assets[0]
        self.assertFalse(asset.matches)
        self.assertEqual(asset.live_amount, "0.5")
        self.assertEqual(asset.computed_amount, "1.0")
        self.assertEqual(asset.difference, "-0.5")

    def test_tiny_dust_difference_still_matches(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._add_event(account.id, btc, "1.00000000", "+")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "1.00000001")])):
            result = reconcile_account(self.session, account)

        self.assertTrue(result.assets[0].matches)

    def test_fee_is_subtracted_from_computed_holding(self) -> None:
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._add_event(account.id, btc, "1.0", "+", fee_amount="0.01")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "0.99")])):
            result = reconcile_account(self.session, account)

        self.assertTrue(result.assets[0].matches)
        self.assertEqual(result.assets[0].computed_amount, "0.99")

    def test_asset_only_on_live_side_is_reported(self) -> None:
        account = self._account()
        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("ETH", "2.5")])):
            result = reconcile_account(self.session, account)

        self.assertEqual(len(result.assets), 1)
        self.assertEqual(result.assets[0].asset_symbol, "ETH")
        self.assertEqual(result.assets[0].computed_amount, "0")
        self.assertFalse(result.assets[0].matches)

    def test_connector_without_fetch_balances_is_unsupported(self) -> None:
        account = self._account()
        with patch("app.core.ledger.reconcile.build_connector", return_value=_NoBalanceConnector()):
            result = reconcile_account(self.session, account)
        self.assertEqual(result.status, "unsupported")

    def test_missing_connector_is_unsupported(self) -> None:
        account = self._account()
        with patch("app.core.ledger.reconcile.build_connector", return_value=None):
            result = reconcile_account(self.session, account)
        self.assertEqual(result.status, "unsupported")

    def test_connector_unavailable_surfaces_real_error(self) -> None:
        account = self._account()
        with patch("app.core.ledger.reconcile.build_connector", return_value=_FailingConnector()):
            result = reconcile_account(self.session, account)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("could not reach exchange", result.message)

    def test_successful_reconcile_stores_a_balance_snapshot_and_marks_the_account(self) -> None:
        account = self._account()
        self.assertIsNone(account.balance_synced_at)

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([Balance("BTC", "1.5")])):
            reconcile_account(self.session, account)

        self.assertIsNotNone(account.balance_synced_at)
        rows = self.session.query(AccountBalance).filter_by(account_id=account.id).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, "1.5")

    def test_store_account_balances_removes_an_asset_no_longer_reported(self) -> None:
        # Simulates a coin fully withdrawn from an exchange: the previous
        # sync stored a row for it, this sync's live_balances no longer
        # mentions it. The stale row must be deleted, not left behind as a
        # phantom holding.
        account = self._account()
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        store_account_balances(self.session, account, [Balance("BTC", "1.0")])
        self.session.commit()
        self.assertEqual(self.session.query(AccountBalance).filter_by(account_id=account.id).count(), 1)

        store_account_balances(self.session, account, [])
        self.session.commit()

        self.assertEqual(self.session.query(AccountBalance).filter_by(account_id=account.id).count(), 0)

    def test_store_account_balances_updates_an_existing_row_in_place(self) -> None:
        account = self._account()
        store_account_balances(self.session, account, [Balance("BTC", "1.0")])
        self.session.commit()

        store_account_balances(self.session, account, [Balance("BTC", "2.5")])
        self.session.commit()

        rows = self.session.query(AccountBalance).filter_by(account_id=account.id).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, "2.5")

    def test_failed_reconcile_never_stores_a_balance_snapshot(self) -> None:
        account = self._account()
        with patch("app.core.ledger.reconcile.build_connector", return_value=_FailingConnector()):
            reconcile_account(self.session, account)

        self.assertIsNone(account.balance_synced_at)
        self.assertEqual(self.session.query(AccountBalance).filter_by(account_id=account.id).count(), 0)

    def test_fiat_assets_are_excluded_from_computed_holdings(self) -> None:
        account = self._account()
        eur = Asset(symbol="EUR", name="Euro", asset_type="FIAT")
        self.session.add(eur)
        self.session.flush()
        self._add_event(account.id, eur, "100", "+")

        with patch("app.core.ledger.reconcile.build_connector", return_value=_FakeConnector([])):
            result = reconcile_account(self.session, account)

        self.assertEqual(result.assets, [])


if __name__ == "__main__":
    unittest.main()
