from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.tax.common import EffectiveEvent, build_supplementary_rows
from app.core.tax.general.adapter import GeneralAdapter
from app.core.ledger.overrides import effective_values
from app.db.models import Account, Asset, Base, Event


class SimplifiedActivityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.account = Account(name="Bitget", kind="exchange", connector_type="manual")
        self.btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add_all([self.account, self.btc])
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _event(self, event_type: str, amount: str, direction: str, **kwargs) -> EffectiveEvent:
        event = Event(
            external_id=f"{event_type}-{amount}-{len(self.session.new)}",
            account_id=self.account.id,
            event_type=event_type,
            direction=direction,
            status="COMPLETE",
            occurred_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            primary_asset_id=self.btc.id,
            primary_amount=amount,
            address_from=kwargs.pop("address_from", "Bitget Spot"),
            provenance="manual",
            normalizer_version="test",
            **kwargs,
        )
        self.session.add(event)
        self.session.flush()
        return EffectiveEvent(event, effective_values(self.session, event)[0])

    def test_standalone_transfer_is_visible_but_neutral_in_general_balance(self) -> None:
        deposit = self._event("DEPOSIT", "1", "+")
        transfer = self._event(
            "TRANSFER",
            "0.5",
            "-",
            address_to="Bitget Futures",
        )
        result = GeneralAdapter().calculate(self.session, 2026, "LEDGER_SUMMARY", "Taxpayer", Path("/tmp"))

        self.assertEqual(result.asset_summary[0].held_quantity, "1")
        self.assertEqual(result.schedule_only_activity_count, 2)
        self.assertEqual([row.event_ids for row in result.transfer_rows], [[transfer.id]])
        self.assertEqual(deposit.id, result.event_schedule_rows[0].event_id)

    def test_missing_prices_warn_without_blocking_readiness(self) -> None:
        self._event("DEPOSIT", "0.1", "+")
        readiness = GeneralAdapter().check_readiness(self.session, 2026)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.activity_count, 1)
        self.assertEqual(readiness.priced_activity_count, 0)
        self.assertGreaterEqual(readiness.warning_count, 1)
        self.assertTrue(all(issue.severity == "warning" for issue in readiness.issues))

    def test_plain_transfer_is_in_supplementary_schedule_without_a_link(self) -> None:
        transfer = self._event("TRANSFER", "2", "-", address_to="Bitget Futures")
        rows, _corrections, schedule, _total, _reconciliation = build_supplementary_rows(
            self.session, [transfer], [], 2026
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_ids, [transfer.id])
        self.assertEqual(schedule[0].event_type, "TRANSFER")


if __name__ == "__main__":
    unittest.main()
