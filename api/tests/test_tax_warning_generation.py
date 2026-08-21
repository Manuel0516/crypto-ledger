from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.tax import GenerateReportIn, generate_report
from app.core.pricing.provider import HistoricalPrice
from app.core.tax.es.adapter import SpainAdapter
from app.db.models import Account, Asset, Base, Event, Fee, Valuation


OCCURRED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class TaxWarningGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.report_dir = Path(tempfile.mkdtemp())

        self.account = Account(
            name="Cake Wallet - BTC",
            kind="wallet",
            connector_type="bitcoin_address",
            status="error",
            last_sync=None,
        )
        self.btc = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", coingecko_id="bitcoin")
        self.eth = Asset(symbol="ETH", name="Ethereum", asset_type="COIN", coingecko_id="ethereum")
        self.usdc = Asset(symbol="USDC", name="USD Coin", asset_type="STABLECOIN", coingecko_id="usd-coin")
        self.session.add_all([self.account, self.btc, self.eth, self.usdc])
        self.session.flush()

        priced_buy = self._event("BUY", "1", "+", self.btc, external_id="priced-buy")
        self.session.add(
            Valuation(
                event_id=priced_buy.id,
                quote_currency="EUR",
                unit_price="100",
                total_value="100",
                requested_timestamp=OCCURRED_AT,
                observation_timestamp=OCCURRED_AT,
                provider="test",
                provider_asset_id="bitcoin",
                method="TEST",
                granularity="day",
            )
        )
        self.session.add(Fee(event_id=priced_buy.id, fee_type="NETWORK_FEE", fee_asset_id=self.eth.id, fee_amount="0.01"))

        # This must remain schedule-only and must never be handed to RP2 as a
        # one-sided disposal.
        self.incomplete_swap = self._event("SWAP", "2", "-", self.usdc, external_id="incomplete-swap")
        self._event("LIQUIDITY", "1", "-", self.btc, subtype="LP_ADD", external_id="liquidity-add")
        self._event("LIQUIDITY", "1", "+", self.btc, subtype="LP_REMOVE", external_id="liquidity-remove")
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        shutil.rmtree(self.report_dir, ignore_errors=True)

    def _event(self, event_type: str, amount: str, direction: str, asset: Asset, *, external_id: str, subtype: str | None = None) -> Event:
        event = Event(
            external_id=external_id,
            account_id=self.account.id,
            event_type=event_type,
            event_subtype=subtype,
            direction=direction,
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=asset.id,
            primary_amount=amount,
            address_from=self.account.name,
            provenance="automatic",
            normalizer_version="test",
        )
        self.session.add(event)
        self.session.flush()
        return event

    def test_warning_only_readiness_is_ready(self) -> None:
        readiness = SpainAdapter().check_readiness(self.session, 2026)

        self.assertTrue(readiness.ready)
        titles = [issue.title for issue in readiness.issues]
        self.assertIn("Fees may be incomplete", titles)
        self.assertIn("Sources not fully synchronized", titles)
        self.assertIn("Incomplete swap", titles)
        self.assertEqual(titles.count("Liquidity activity needs review"), 2)

    def test_spain_report_generates_with_all_warning_types(self) -> None:
        def fake_runner(_executable: str, _config: Path, _input: Path, output_dir: Path, _method: str) -> Path:
            output_dir.mkdir(parents=True, exist_ok=True)
            report_path = output_dir / "report_fifo_rp2_full_report.ods"
            report_path.write_bytes(b"test RP2 report")
            return report_path

        with (
            patch("app.api.tax.TAX_REPORTS_DIR", self.report_dir),
            patch("app.core.tax.es.adapter.runner.run", side_effect=fake_runner),
            patch("app.core.tax.es.adapter.ods_io.parse_output", return_value=[]),
            patch(
                "app.core.tax.rp2_runner.ods_io.get_historical_prices",
                return_value={
                    "EUR": HistoricalPrice(
                        unit_price=Decimal("2"),
                        method="TEST",
                        observation_timestamp=OCCURRED_AT,
                        granularity="day",
                    )
                },
            ),
        ):
            report = generate_report(GenerateReportIn(country="ES", tax_year=2026, language="en"), self.session)

        self.assertEqual(report["status"], "complete")
        self.assertTrue(any("missing its incoming asset" in warning.lower() for warning in report["warnings"]))
        self.assertEqual(sum("remains in the activity schedule" in warning for warning in report["warnings"]), 2)
        self.assertEqual(report["summary"]["event_schedule_total"], 4)
        self.assertTrue((self.report_dir / str(report["id"]) / "report.pdf").exists())


if __name__ == "__main__":
    unittest.main()
