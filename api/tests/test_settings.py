from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.settings import SecretConfirmation, SettingsUpdate, delete_secret, list_secrets, reveal_secret, store_explorer_key, store_provider_key, ProviderKeyInput, update_settings
from app.api.overview import overview
from app.core.ledger.connectors import build_connector
from app.core.settings import DEFAULT_VALUATION_CURRENCIES, get_or_create_settings, valuation_currencies
from app.db.models import Account, Asset, Base, Event, Valuation


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_defaults_are_safe_and_include_required_valuations(self) -> None:
        settings = get_or_create_settings(self.session)
        self.assertTrue(settings.sync_enabled)
        self.assertEqual(settings.minimum_activity_value, "0.05")
        self.assertEqual(settings.minimum_activity_currency, "EUR")
        self.assertEqual(valuation_currencies(settings), DEFAULT_VALUATION_CURRENCIES)
        self.assertEqual(settings.evidence_retention_policy, "indefinite")

    def test_settings_update_persists_runtime_configuration(self) -> None:
        result = update_settings(
            SettingsUpdate(
                sync_enabled=False,
                sync_interval_minutes=45,
                valuation_currencies=["EUR", "SEK", "USD"],
                display_currency="USD",
                backup_hour_utc=5,
                backup_retention_daily=14,
                backup_retention_weekly=8,
                backup_retention_monthly=24,
                price_timeout_seconds=20,
                ui_theme="dark",
                default_timezone="Europe/Stockholm",
                minimum_activity_value="0.10",
                minimum_activity_currency="USD",
            ),
            self.session,
        )
        self.assertFalse(result["sync_enabled"])
        self.assertEqual(result["sync_interval_minutes"], 45)
        self.assertEqual(result["valuation_currencies"], ["EUR", "SEK", "USD"])
        self.assertEqual(result["display_currency"], "USD")
        self.assertEqual(result["backup_retention_monthly"], 24)
        self.assertEqual(result["ui_theme"], "dark")
        self.assertEqual(result["minimum_activity_value"], 0.1)
        self.assertEqual(result["minimum_activity_currency"], "USD")

    def test_required_valuation_currencies_cannot_be_removed(self) -> None:
        with self.assertRaises(ValidationError):
            SettingsUpdate(valuation_currencies=["USD"])

    def test_overview_uses_the_configured_display_currency(self) -> None:
        asset = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", coingecko_id="bitcoin")
        self.session.add(asset)
        self.session.flush()
        occurred_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
        event = Event(
            external_id="settings-overview-test",
            event_type="RECEIVE",
            direction="+",
            status="COMPLETE",
            occurred_at=occurred_at,
            primary_asset_id=asset.id,
            primary_amount="2",
            address_from="Test source",
            provenance="manual",
            normalizer_version="test",
        )
        self.session.add(event)
        self.session.flush()
        for currency, price in (("EUR", "10"), ("SEK", "100"), ("USD", "12")):
            self.session.add(
                Valuation(
                    event_id=event.id,
                    quote_currency=currency,
                    unit_price=price,
                    total_value=str(int(price) * 2),
                    requested_timestamp=occurred_at,
                    observation_timestamp=occurred_at,
                    provider="test",
                    provider_asset_id="bitcoin",
                    method="MANUAL",
                )
            )
        self.session.commit()

        update_settings(SettingsUpdate(display_currency="USD", valuation_currencies=["EUR", "SEK", "USD"]), self.session)
        # overview() fetches a live "current price" snapshot on top of the
        # historical Valuation rows above (see app.api.overview) — mock the
        # provider so this test verifies currency selection, not network
        # access to a real price API. An empty result here means overview()
        # falls back to the most recent historical Valuation, which is
        # exactly the deterministic value this test seeded.
        with patch("app.api.overview.configured_price_provider") as mock_provider:
            mock_provider.return_value.fetch_current.return_value = {}
            result = overview(self.session)

        self.assertEqual(result["display_currency"], "USD")
        self.assertEqual(result["portfolio_display"], 24.0)
        self.assertEqual(result["assets"][0]["value_display"], 24.0)

    def test_app_vault_key_can_be_revealed_and_permanently_deleted(self) -> None:
        with patch.dict("os.environ", {"APP_SECRET_KEY": Fernet.generate_key().decode()}, clear=False):
            store_provider_key(ProviderKeyInput(value="test-provider-key"), self.session)
            items = list_secrets(self.session)
            provider = next(item for item in items if item["id"] == "price-provider-key")
            self.assertTrue(provider["revealable"])
            self.assertEqual(
                reveal_secret("price-provider-key", SecretConfirmation(confirmed=True), self.session)["value"],
                "test-provider-key",
            )
            self.assertTrue(delete_secret("price-provider-key", SecretConfirmation(confirmed=True), self.session)["deleted"])
            self.assertFalse(next(item for item in list_secrets(self.session) if item["id"] == "price-provider-key")["configured"])

    def test_explorer_key_is_application_wide_and_inherited_by_evm_sources(self) -> None:
        with patch.dict("os.environ", {"APP_SECRET_KEY": Fernet.generate_key().decode()}, clear=False):
            store_explorer_key("etherscan", ProviderKeyInput(value="shared-explorer-key"), self.session)
            account = Account(
                name="Ethereum wallet",
                kind="wallet",
                connector_type="evm_address",
                chain_network="ethereum",
                address="0x0000000000000000000000000000000000000001",
                status="not_configured",
            )
            self.session.add(account)
            self.session.commit()

            connector = build_connector(account, self.session)
            self.assertEqual(connector.config.get("explorer_api_key"), "shared-explorer-key")
            configured = next(item for item in list_secrets(self.session) if item["id"] == "explorer:etherscan")
            self.assertTrue(configured["configured"])
            self.assertEqual(
                reveal_secret("explorer:etherscan", SecretConfirmation(confirmed=True), self.session)["value"],
                "shared-explorer-key",
            )


if __name__ == "__main__":
    unittest.main()
