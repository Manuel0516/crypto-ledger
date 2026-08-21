from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.overview import overview
from app.db.models import Account, AccountBalance, Asset, Base, Event

OCCURRED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)


class OverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _account(self, *, live: bool) -> Account:
        account = Account(
            name="Test exchange",
            kind="exchange",
            connector_type="binance_live",
            status="connected",
            balance_synced_at=OCCURRED_AT if live else None,
        )
        self.session.add(account)
        self.session.flush()
        return account

    def _event(self, account_id: int | None, asset: Asset, amount: str, direction: str = "+") -> None:
        event = Event(
            external_id=f"evt-{account_id}-{asset.symbol}-{amount}-{direction}",
            account_id=account_id,
            event_type="RECEIVE" if direction == "+" else "SEND",
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

    def test_live_account_shows_the_snapshot_not_the_computed_ledger(self) -> None:
        # The account's events would compute to 5.0 BTC, but its live-balance
        # snapshot says 1.0 — the exact "doubled total" / drift bug this
        # feature exists to close. The snapshot must win.
        account = self._account(live=True)
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._event(account.id, btc, "5.0")
        self.session.add(AccountBalance(account_id=account.id, asset_id=btc.id, amount="1.0"))
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["amount"], 1.0)

    def test_account_without_a_live_snapshot_still_uses_computed_events(self) -> None:
        account = self._account(live=False)
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._event(account.id, btc, "0.25")
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["amount"], 0.25)

    def test_coin_removed_from_a_live_wallet_does_not_linger_as_a_phantom_holding(self) -> None:
        # Old events show BTC ever having passed through this account, but no
        # AccountBalance row exists for it any more (store_account_balances
        # deletes a stale row once the source stops reporting that asset) —
        # it must not appear just because it once did.
        account = self._account(live=True)
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self._event(account.id, btc, "2.0")
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(result["assets"], [])
        self.assertEqual(result["portfolio_eur"], 0.0)

    def test_live_and_computed_accounts_do_not_double_count_the_same_asset(self) -> None:
        live_account = self._account(live=True)
        computed_account = self._account(live=False)
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self.session.add(AccountBalance(account_id=live_account.id, asset_id=btc.id, amount="1.0"))
        self._event(computed_account.id, btc, "0.5")
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["amount"], 1.5)

    def test_live_addresses_are_returned_separately_and_aggregate_once(self) -> None:
        first = self._account(live=True)
        second = self._account(live=True)
        eth = Asset(symbol="ETH", name="Ethereum", asset_type="COIN", network="Ethereum")
        self.session.add(eth)
        self.session.flush()
        self.session.add_all(
            [
                AccountBalance(account_id=first.id, asset_id=eth.id, amount="1.25"),
                AccountBalance(account_id=second.id, asset_id=eth.id, amount="2.75"),
            ]
        )
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(result["assets"][0]["amount"], 4.0)
        self.assertEqual([account["balances"][0]["amount"] for account in result["accounts"]], [1.25, 2.75])

    def test_nft_events_remain_visible_for_a_live_address(self) -> None:
        account = self._account(live=True)
        collectible = Asset(
            symbol="BAYC",
            name="Bored Ape Yacht Club",
            asset_type="NFT",
            network="Ethereum",
            contract_address="0xcollection",
        )
        self.session.add(collectible)
        self.session.flush()
        self._event(account.id, collectible, "1.0")
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["asset_type"], "NFT")
        self.assertEqual(result["assets"][0]["amount"], 1.0)
        self.assertEqual(result["accounts"][0]["balances"][0]["symbol"], "BAYC")

    def test_events_with_no_owning_account_are_always_computed(self) -> None:
        # A manual/legacy event with account_id=None has no account to have
        # a live balance, so it must be computed regardless of whether any
        # other account in the system has a live snapshot.
        live_account = self._account(live=True)
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(btc)
        self.session.flush()
        self.session.add(AccountBalance(account_id=live_account.id, asset_id=btc.id, amount="1.0"))
        self._event(None, btc, "0.3")
        self.session.commit()

        result = overview(self.session)

        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["amount"], 1.3)


if __name__ == "__main__":
    unittest.main()
