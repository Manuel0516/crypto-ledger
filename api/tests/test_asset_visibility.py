from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.assets import AssetVisibilityPatch, list_assets, update_asset_visibility
from app.api.events import list_events
from app.api.overview import overview
from app.core.tax.common import load_events_through
from app.core.ledger.spam import looks_like_mass_distribution_input
from app.db.models import Account, AccountBalance, Asset, Base, Event, RawEvent


OCCURRED_AT = datetime(2026, 8, 21, tzinfo=timezone.utc)


def mass_distribution_input() -> str:
    addresses = ["0" * 24 + f"{number:040x}" for number in range(1, 101)]
    return "0x866a2476" + "0" * 62 + "40" + "0" * 64 + "0" * 62 + "64" + "".join(addresses)


class AssetVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _event(self, asset: Asset, external_id: str, *, tx_hash: str | None = None, raw: RawEvent | None = None) -> Event:
        event = Event(
            external_id=external_id,
            raw_event=raw,
            event_type="DEPOSIT",
            direction="+",
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=asset.id,
            primary_amount="1",
            address_from="Wallet",
            provenance="automatic",
            normalizer_version="test",
            tx_hash=tx_hash,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def test_blocked_asset_is_hidden_from_activity_overview_and_tax_inputs(self) -> None:
        wallet = Account(name="Wallet", kind="wallet", connector_type="manual", balance_synced_at=OCCURRED_AT)
        visible = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN")
        blocked = Asset(symbol="SCAM", name="Scam token", asset_type="TOKEN", network="Ethereum", is_blocked=True)
        self.session.add_all([wallet, visible, blocked])
        self.session.flush()
        self.session.add_all([
            AccountBalance(account_id=wallet.id, asset_id=visible.id, amount="1"),
            AccountBalance(account_id=wallet.id, asset_id=blocked.id, amount="1"),
        ])
        self._event(visible, "visible")
        self._event(blocked, "blocked")
        self.session.commit()

        activity = list_events(limit=50, session=self.session)
        self.assertEqual([item["asset_symbol"] for item in activity["items"]], ["BTC"])
        result = overview(self.session)
        self.assertEqual([item["symbol"] for item in result["assets"]], ["BTC"])
        self.assertEqual([item["symbol"] for item in result["accounts"][0]["balances"]], ["BTC"])
        self.assertEqual([event.primary_asset.symbol for event in load_events_through(self.session, 2026)], ["BTC"])

    def test_asset_can_be_blocked_and_unblocked_without_deleting_it(self) -> None:
        asset = Asset(symbol="SCAM", name="Scam token", asset_type="TOKEN")
        self.session.add(asset)
        self.session.commit()

        update_asset_visibility(asset.id, AssetVisibilityPatch(is_blocked=True), self.session)
        self.assertTrue(self.session.get(Asset, asset.id).is_blocked)
        update_asset_visibility(asset.id, AssetVisibilityPatch(is_blocked=False), self.session)
        self.assertFalse(self.session.get(Asset, asset.id).is_blocked)

    def test_mass_distribution_input_is_a_review_signal_for_the_asset(self) -> None:
        self.assertTrue(looks_like_mass_distribution_input(mass_distribution_input()))
        asset = Asset(symbol="SCAM", name="Scam token", asset_type="TOKEN", network="BSC", contract_address="0xscam")
        raw = RawEvent(
            source_id="wallet",
            external_id="raw-spam",
            payload_json=json.dumps({"hash": "0xspam", "input": mass_distribution_input()}),
            payload_hash="hash",
            connector_version="test",
        )
        self.session.add(asset)
        self.session.flush()
        self._event(asset, "spam", tx_hash="0xspam", raw=raw)
        self.session.commit()

        listed = list_assets(session=self.session)
        self.assertTrue(listed[0]["spam_suspected"])
        self.assertFalse(listed[0]["is_blocked"])


if __name__ == "__main__":
    unittest.main()
