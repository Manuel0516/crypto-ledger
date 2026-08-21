from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.accounts import NewAccount, _serialize, create_account
from app.db.models import Account, AccountBalance, Asset, Base, Event, Fee

OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


class AccountsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _asset(self, symbol: str, network: str | None = None) -> Asset:
        asset = Asset(symbol=symbol, name=symbol, network=network)
        self.session.add(asset)
        self.session.flush()
        return asset

    def test_exchange_balance_label_names_the_wallet_by_account_and_symbol(self) -> None:
        # plan spec: "Bitget - USDT" instead of a raw address for an
        # exchange-held asset — this is what the Linked Accounts card and
        # the activity form both read to name a wallet.
        account = Account(name="Bitget", kind="exchange", connector_type="bitget_live", status="connected")
        self.session.add(account)
        self.session.flush()
        usdt = self._asset("USDT")
        self.session.add(AccountBalance(account_id=account.id, asset_id=usdt.id, amount="1234.56"))
        self.session.commit()

        payload = _serialize(self.session, account)

        self.assertEqual(len(payload["balances"]), 1)
        self.assertEqual(payload["balances"][0]["wallet_label"], "Bitget - USDT")
        self.assertEqual(payload["balances"][0]["amount"], "1234.56")

    def test_exchange_fees_are_aggregated_by_asset_across_events(self) -> None:
        account = Account(name="Binance", kind="exchange", connector_type="binance_live", status="connected")
        self.session.add(account)
        self.session.flush()
        btc = self._asset("BTC")

        for amount in ("0.0001", "0.0002"):
            event = Event(
                external_id=f"binance:withdrawal-{amount}",
                account_id=account.id,
                event_type="WITHDRAWAL",
                direction="-",
                status="COMPLETE",
                occurred_at=OCCURRED_AT,
                primary_asset_id=btc.id,
                primary_amount=amount,
                address_from="Binance",
                normalizer_version="binance-live-test",
            )
            self.session.add(event)
            self.session.flush()
            self.session.add(Fee(event_id=event.id, fee_type="EXCHANGE_FEE", fee_asset_id=btc.id, fee_amount="0.00001"))
        self.session.commit()

        payload = _serialize(self.session, account)

        self.assertEqual(len(payload["fees"]), 1)
        self.assertEqual(payload["fees"][0]["symbol"], "BTC")
        self.assertEqual(payload["fees"][0]["count"], 2)
        self.assertEqual(payload["fees"][0]["amount"], "0.00002")

    def test_event_count_excludes_blocked_spam_assets(self) -> None:
        account = Account(name="Bitget", kind="exchange", connector_type="bitget_live", status="connected")
        self.session.add(account)
        self.session.flush()
        real_asset = self._asset("USDT")
        spam_asset = Asset(symbol="FREEAIR", name="Free Airdrop", network=None, is_blocked=True)
        self.session.add(spam_asset)
        self.session.flush()
        self.session.add_all(
            [
                Event(
                    external_id="bitget:deposit-1",
                    account_id=account.id,
                    event_type="DEPOSIT",
                    direction="+",
                    status="COMPLETE",
                    occurred_at=OCCURRED_AT,
                    primary_asset_id=real_asset.id,
                    primary_amount="10",
                address_from="Bitget",
                    normalizer_version="bitget-live-test",
                ),
                Event(
                    external_id="bitget:deposit-2",
                    account_id=account.id,
                    event_type="DEPOSIT",
                    direction="+",
                    status="COMPLETE",
                    occurred_at=OCCURRED_AT,
                    primary_asset_id=spam_asset.id,
                    primary_amount="999999",
                address_from="Bitget",
                    normalizer_version="bitget-live-test",
                ),
            ]
        )
        self.session.commit()

        payload = _serialize(self.session, account)

        self.assertEqual(payload["event_count"], 1)

    def test_create_account_accepts_bitget_and_binance_live_connectors(self) -> None:
        bitget = create_account(NewAccount(name="Bitget", connector_type="bitget_live"), self.session)
        binance = create_account(NewAccount(name="Binance", connector_type="binance_live"), self.session)

        self.assertEqual(bitget["kind"], "exchange")
        self.assertEqual(binance["kind"], "exchange")
        self.assertTrue(bitget["syncable"])
        self.assertTrue(binance["syncable"])

    def test_create_account_rejects_lightning_and_monero_connector_types(self) -> None:
        # Simplification: Lightning and Monero are no longer addable sources
        # (see api/accounts.py's _DEFAULT_KIND comment) — Monero tracking
        # goes through the plain "manual" connector type instead.
        for connector_type in ("lightning_node", "lightning_nwc", "monero_rpc"):
            with self.assertRaises(HTTPException):
                create_account(NewAccount(name="x", connector_type=connector_type), self.session)


if __name__ == "__main__":
    unittest.main()
