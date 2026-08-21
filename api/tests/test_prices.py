from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.prices import historical_prices
from app.core.pricing.provider import HistoricalPrice
from app.db.models import Asset, Base


class _Provider:
    name = "Test market"


class HistoricalPriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_lookup_returns_unit_prices_and_positive_totals_for_outgoing_amounts(self) -> None:
        self.session.add(
            Asset(
                symbol="BTC",
                name="Bitcoin",
                asset_type="COIN",
                network="Bitcoin",
                coingecko_id="bitcoin",
            )
        )
        self.session.flush()
        observed = datetime(2026, 8, 20, tzinfo=timezone.utc)
        with (
            patch("app.api.prices.configured_price_provider", return_value=_Provider()),
            patch(
                "app.api.prices.get_historical_prices",
                return_value={
                    "EUR": HistoricalPrice(
                        unit_price=Decimal("25000.123"),
                        method="DAILY_REFERENCE",
                        observation_timestamp=observed,
                        granularity="day",
                    )
                },
            ),
        ):
            result = historical_prices(
                symbol="BTC",
                at=observed,
                amount="-0.5",
                network=None,
                currencies="EUR,SEK",
                session=self.session,
            )

        self.assertEqual(result["prices"]["EUR"]["unit_price"], "25000.123")
        self.assertEqual(result["prices"]["EUR"]["total_value"], "12500.06")
        self.assertEqual(result["missing"], ["SEK"])


if __name__ == "__main__":
    unittest.main()
