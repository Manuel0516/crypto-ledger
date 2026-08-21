from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.tax import GenerateReportIn, generate_report
from app.db.models import Account, Asset, Base, Event

OCCURRED_AT = datetime(2026, 3, 1, tzinfo=timezone.utc)


class GenerateReportOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.reports_dir = Path(tempfile.mkdtemp())

        account = Account(name="Bitget", kind="exchange", connector_type="bitget_live")
        btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add_all([account, btc])
        self.session.flush()
        # An unlinked DEPOSIT is a valid independent schedule activity.
        event = Event(
            external_id="ambiguous-deposit",
            account_id=account.id,
            event_type="DEPOSIT",
            direction="+",
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=btc.id,
            primary_amount="0.1",
            address_from="Bitget",
            provenance="automatic",
            normalizer_version="test",
        )
        self.session.add(event)
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        shutil.rmtree(self.reports_dir, ignore_errors=True)

    def _generate(self, *, acknowledge: bool):
        body = GenerateReportIn(country="GENERAL", tax_year=2026, acknowledge_blocking_issues=acknowledge)
        with patch("app.api.tax.TAX_REPORTS_DIR", self.reports_dir):
            return generate_report(body, self.session)

    def test_unlinked_deposit_generates_without_acknowledgement(self) -> None:
        report = self._generate(acknowledge=False)
        self.assertEqual(report["status"], "complete")

    def test_legacy_acknowledgement_field_is_ignored(self) -> None:
        report = self._generate(acknowledge=True)
        self.assertEqual(report["status"], "complete")
        self.assertFalse(any("unresolved blocking readiness issue" in w for w in report["warnings"]))
        self.assertFalse(any("Unclassified transfer" in w for w in report["warnings"]))

    def test_a_ready_year_ignores_the_flag_and_needs_no_override_note(self) -> None:
        # A tax year with no activities also remains readable and the legacy
        # acknowledgement field does not affect the result path.
        report = self._generate(acknowledge=True)
        # 2025 has no events at all, so it's trivially "ready".
        body = GenerateReportIn(country="GENERAL", tax_year=2025, acknowledge_blocking_issues=True)
        with patch("app.api.tax.TAX_REPORTS_DIR", self.reports_dir):
            empty_year_report = generate_report(body, self.session)
        self.assertEqual(empty_year_report["status"], "complete")
        self.assertFalse(any("unresolved blocking readiness issue" in w for w in empty_year_report["warnings"]))
        self.assertIsNotNone(report)


if __name__ == "__main__":
    unittest.main()
