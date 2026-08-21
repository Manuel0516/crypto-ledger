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

from app.api.issues import retry_pricing_issues
from app.core.assets.registry import get_or_create_asset
from app.core.ledger.service import refresh_valuations
from app.core.pricing.cache import get_historical_prices
from app.core.pricing.coingecko import CoinGeckoProvider
from app.core.pricing.provider import DayPrices, HistoricalPrice
from app.db.models import Asset, Base, Event, Issue, PriceObservation, Valuation

OCCURRED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _FakeProvider:
    name = "Fake"

    def __init__(self, day_prices: DayPrices | None = None, resolved_id: str | None = "not-configured"):
        self._day_prices = day_prices
        self._resolved_id = resolved_id

    def fetch_day(self, provider_asset_id, at, quote_currencies):
        return self._day_prices

    def resolve_symbol(self, symbol):
        return self._resolved_id


class _CurrentPriceProvider:
    name = "Current fake"

    def fetch_current(self, provider_asset_ids, quote_currencies):
        return {
            provider_asset_id: {currency: Decimal("100") for currency in quote_currencies}
            for provider_asset_id in provider_asset_ids
        }


class _NoResolveProvider:
    """A provider with no resolve_symbol at all — the pre-existing shape,
    must keep working exactly as before (issue flagged, no crash)."""

    name = "Fake"

    def fetch_day(self, provider_asset_id, at, quote_currencies):
        return None


class _IntradayProvider:
    name = "Intraday fake"

    def __init__(self):
        self.intraday_calls = 0
        self.day_calls = 0

    def fetch_near_timestamp(self, provider_asset_id, at, quote_currencies):
        self.intraday_calls += 1
        observed_at = at.replace(minute=0, second=0, microsecond=0)
        return {
            currency: HistoricalPrice(
                unit_price=Decimal("42.50"),
                method="NEAREST_HOUR",
                observation_timestamp=observed_at,
                granularity="hour",
            )
            for currency in quote_currencies
        }

    def fetch_day(self, provider_asset_id, at, quote_currencies):
        self.day_calls += 1
        return DayPrices(self.name, provider_asset_id, "DAILY_REFERENCE", {currency: Decimal("40") for currency in quote_currencies})


def _event(session, asset: Asset, amount: str = "1.0") -> Event:
    event = Event(
        external_id=f"evt-{asset.symbol}-{asset.id}",
        event_type="RECEIVE",
        direction="+",
        status="COMPLETE",
        occurred_at=OCCURRED_AT,
        primary_asset_id=asset.id,
        primary_amount=amount,
        address_from="Test",
        provenance="manual",
        normalizer_version="test",
    )
    session.add(event)
    session.flush()
    return event


class AssetResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_unknown_asset_resolves_via_provider_and_prices_the_event(self) -> None:
        asset = Asset(symbol="ADA", name="ADA", asset_type="COIN")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset)

        day_prices = DayPrices(provider="Fake", provider_asset_id="cardano", method="DAILY_REFERENCE", prices={"EUR": Decimal("0.5")})
        provider = _FakeProvider(day_prices=day_prices, resolved_id="cardano")
        with patch("app.core.ledger.service.configured_price_provider", return_value=provider):
            refresh_valuations(self.session, event, currencies=("EUR",))

        self.assertEqual(asset.coingecko_id, "cardano")
        valuations = self.session.query(Valuation).filter_by(event_id=event.id).all()
        self.assertEqual(len(valuations), 1)
        self.assertEqual(valuations[0].unit_price, "0.5")
        self.assertEqual(
            self.session.query(Issue).filter_by(event_id=event.id, title="Unknown asset — no price source", resolved=False).count(),
            0,
        )

    def test_current_portfolio_price_is_separate_from_historical_event_price(self) -> None:
        asset = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", coingecko_id="bitcoin")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset, amount="2")
        self.session.add(
            Valuation(
                event_id=event.id,
                quote_currency="EUR",
                unit_price="10",
                total_value="20",
                requested_timestamp=OCCURRED_AT,
                observation_timestamp=OCCURRED_AT,
                provider="historical-test",
                provider_asset_id="bitcoin",
                method="DAILY_REFERENCE",
            )
        )
        self.session.commit()

        with patch("app.api.overview.configured_price_provider", return_value=_CurrentPriceProvider()):
            from app.api.overview import overview

            result = overview(self.session)

        self.assertEqual(result["portfolio_eur"], 200.0)
        self.assertEqual(result["assets"][0]["value_eur"], 200.0)

    def test_direct_fiat_execution_uses_exact_price_without_provider_lookup(self) -> None:
        asset = Asset(symbol="NEWCOIN", name="NEWCOIN", asset_type="COIN")
        eur = Asset(symbol="EUR", name="Euro", asset_type="FIAT")
        self.session.add_all((asset, eur))
        self.session.flush()
        event = _event(self.session, asset, amount="2")
        event.secondary_asset_id = eur.id
        event.secondary_amount = "123.45"

        with patch("app.core.ledger.service.configured_price_provider") as provider:
            refresh_valuations(self.session, event, currencies=("EUR",))

        provider.assert_not_called()
        valuation = event.valuations[0]
        self.assertEqual((valuation.unit_price, valuation.total_value), ("61.725", "123.45"))
        self.assertEqual((valuation.provider, valuation.method, valuation.confidence), ("exchange_execution", "EXACT_EXECUTION", "high"))
        self.assertEqual((valuation.granularity, valuation.observation_timestamp), ("exact", event.occurred_at))

    def test_crypto_quoted_execution_uses_exact_trade_ratio_then_quote_fx(self) -> None:
        asset = Asset(symbol="UNMAPPED", name="Unmapped", asset_type="COIN")
        tether = Asset(symbol="USDT", name="Tether", asset_type="STABLECOIN", coingecko_id="tether")
        self.session.add_all((asset, tether))
        self.session.flush()
        event = _event(self.session, asset, amount="2")
        event.event_type = "BUY"
        event.secondary_asset_id = tether.id
        event.secondary_amount = "300"
        provider = _FakeProvider(
            day_prices=DayPrices(provider="Fake", provider_asset_id="tether", method="DAILY_REFERENCE", prices={"EUR": Decimal("0.92")}),
            resolved_id=None,
        )

        with patch("app.core.ledger.service.configured_price_provider", return_value=provider):
            refresh_valuations(self.session, event, currencies=("EUR",))

        valuation = event.valuations[0]
        self.assertEqual((valuation.unit_price, valuation.total_value), ("138.00", "276.00"))
        self.assertEqual((valuation.provider, valuation.method, valuation.granularity), ("exchange_execution+Fake", "DERIVED_FX", "execution×day"))
        self.assertIn("UNMAPPED/USDT; fx:tether", valuation.provider_asset_id)
        self.assertIsNone(asset.coingecko_id, "the primary coin need not be mapped when the exact execution is usable")

    def test_recent_intraday_observation_is_cached_per_requested_hour(self) -> None:
        provider = _IntradayProvider()
        first = get_historical_prices(self.session, provider, "bitcoin", OCCURRED_AT, ["EUR"])
        second = get_historical_prices(self.session, provider, "bitcoin", OCCURRED_AT, ["EUR"])

        self.assertEqual(provider.intraday_calls, 1)
        self.assertEqual(provider.day_calls, 0)
        self.assertEqual(first["EUR"].method, "NEAREST_HOUR")
        self.assertEqual(second["EUR"].unit_price, Decimal("42.50"))
        cached = self.session.query(PriceObservation).one()
        self.assertEqual((cached.observation_date, cached.granularity), ("2026-08-20T00", "hour"))

    def test_exchange_staking_receipt_assets_have_non_ambiguous_price_ids(self) -> None:
        assets = {symbol: get_or_create_asset(self.session, symbol) for symbol in ("BETH", "WBETH", "BNSOL", "BGB", "FDUSD")}
        self.assertEqual(
            {symbol: asset.coingecko_id for symbol, asset in assets.items()},
            {
                "BETH": "binance-eth-staking",
                "WBETH": "wrapped-beacon-eth",
                "BNSOL": "binance-staked-sol",
                "BGB": "bitget-token",
                "FDUSD": "first-digital-usd",
            },
        )

    def test_unresolvable_asset_still_flags_the_issue(self) -> None:
        asset = Asset(symbol="ZZZFAKECOIN", name="ZZZFAKECOIN", asset_type="COIN")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset)

        provider = _FakeProvider(resolved_id=None)
        with patch("app.core.ledger.service.configured_price_provider", return_value=provider):
            refresh_valuations(self.session, event, currencies=("EUR",))

        self.assertIsNone(asset.coingecko_id)
        issue = self.session.query(Issue).filter_by(event_id=event.id, title="Unknown asset — no price source", resolved=False).one_or_none()
        self.assertIsNotNone(issue)

    def test_provider_without_resolve_symbol_behaves_exactly_as_before(self) -> None:
        asset = Asset(symbol="ZZZFAKECOIN2", name="ZZZFAKECOIN2", asset_type="COIN")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset)

        with patch("app.core.ledger.service.configured_price_provider", return_value=_NoResolveProvider()):
            refresh_valuations(self.session, event, currencies=("EUR",))

        self.assertIsNone(asset.coingecko_id)
        self.assertIsNotNone(
            self.session.query(Issue).filter_by(event_id=event.id, title="Unknown asset — no price source", resolved=False).one_or_none()
        )

    def test_retry_pricing_endpoint_clears_a_backlog_issue_once_the_asset_resolves(self) -> None:
        asset = Asset(symbol="ADA", name="ADA", asset_type="COIN")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset)
        self.session.add(
            Issue(
                event_id=event.id,
                severity="warning",
                title="Unknown asset — no price source",
                detail="stale",
                resolved=False,
            )
        )
        self.session.commit()

        day_prices = DayPrices(provider="Fake", provider_asset_id="cardano", method="DAILY_REFERENCE", prices={"EUR": Decimal("0.5")})
        provider = _FakeProvider(day_prices=day_prices, resolved_id="cardano")
        with patch("app.core.ledger.service.configured_price_provider", return_value=provider):
            result = retry_pricing_issues(self.session)

        self.assertEqual(result["retried"], 1)
        self.assertEqual(result["resolved"], 1)
        self.assertEqual(asset.coingecko_id, "cardano")

    def test_retry_pricing_endpoint_leaves_a_still_unresolvable_issue_alone(self) -> None:
        asset = Asset(symbol="ZZZFAKECOIN3", name="ZZZFAKECOIN3", asset_type="COIN")
        self.session.add(asset)
        self.session.flush()
        event = _event(self.session, asset)
        self.session.add(
            Issue(event_id=event.id, severity="warning", title="Unknown asset — no price source", detail="stale", resolved=False)
        )
        self.session.commit()

        with patch("app.core.ledger.service.configured_price_provider", return_value=_FakeProvider(resolved_id=None)):
            result = retry_pricing_issues(self.session)

        self.assertEqual(result["retried"], 1)
        self.assertEqual(result["resolved"], 0)
        issue = self.session.query(Issue).filter_by(event_id=event.id, resolved=False).one_or_none()
        self.assertIsNotNone(issue)


class _FakeSearchResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class CoinGeckoResolveSymbolTests(unittest.TestCase):
    def test_picks_the_first_exact_ticker_match_by_market_cap_order(self) -> None:
        # /search is documented to return coins sorted by market cap
        # descending — a low-cap ticker squatter listed after the real coin
        # must not win.
        payload = {
            "coins": [
                {"id": "cardano", "symbol": "ADA", "market_cap_rank": 8},
                {"id": "some-squatter-token", "symbol": "ADA", "market_cap_rank": 4000},
            ]
        }
        provider = CoinGeckoProvider()
        with patch("app.core.pricing.coingecko.httpx.get", return_value=_FakeSearchResponse(payload)):
            self.assertEqual(provider.resolve_symbol("ada"), "cardano")

    def test_ignores_a_fuzzy_name_match_with_a_different_ticker(self) -> None:
        payload = {"coins": [{"id": "ethereum-classic", "symbol": "ETC", "market_cap_rank": 20}]}
        provider = CoinGeckoProvider()
        with patch("app.core.pricing.coingecko.httpx.get", return_value=_FakeSearchResponse(payload)):
            self.assertIsNone(provider.resolve_symbol("ETH"))

    def test_no_coins_in_response_returns_none(self) -> None:
        provider = CoinGeckoProvider()
        with patch("app.core.pricing.coingecko.httpx.get", return_value=_FakeSearchResponse({"coins": []})):
            self.assertIsNone(provider.resolve_symbol("ADA"))

    def test_network_failure_returns_none_instead_of_raising(self) -> None:
        import httpx

        provider = CoinGeckoProvider()
        with patch("app.core.pricing.coingecko.httpx.get", side_effect=httpx.ConnectError("down")):
            self.assertIsNone(provider.resolve_symbol("ADA"))


if __name__ == "__main__":
    unittest.main()
