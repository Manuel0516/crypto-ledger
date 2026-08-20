from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.db.models import AppSettings


DEFAULT_VALUATION_CURRENCIES = ("EUR", "SEK")
SUPPORTED_PRICE_PROVIDERS = ("coingecko",)
THEME_OPTIONS = ("system", "light", "dark")
EVIDENCE_RETENTION_POLICY = "indefinite"
DEFAULT_RP2_PLUGINS = ("rp2_es",)


def valuation_currencies(settings: AppSettings) -> tuple[str, ...]:
    """Return a normalized quote-currency list without allowing the ledger's
    required EUR/SEK history to be disabled."""
    try:
        configured = json.loads(settings.valuation_currencies_json)
    except (TypeError, json.JSONDecodeError):
        configured = []
    values = [str(currency).strip().upper() for currency in configured if str(currency).strip()]
    values = list(dict.fromkeys([*DEFAULT_VALUATION_CURRENCIES, *values]))
    return tuple(values)


def reset_settings(settings: AppSettings) -> None:
    settings.sync_interval_minutes = 15
    settings.sync_enabled = True
    settings.display_currency = "EUR"
    settings.valuation_currencies_json = json.dumps(DEFAULT_VALUATION_CURRENCIES)
    settings.price_provider = "coingecko"
    settings.price_provider_api_key_encrypted = None
    settings.price_timeout_seconds = 10
    settings.backup_hour_utc = 3
    settings.backup_verify_after_create = True
    settings.backup_retention_daily = 7
    settings.backup_retention_weekly = 4
    settings.backup_retention_monthly = 12
    settings.ui_theme = "system"
    settings.default_timezone = "UTC"
    settings.evidence_retention_policy = EVIDENCE_RETENTION_POLICY
    settings.default_country = None
    settings.default_tax_year = None
    settings.taxpayer_name = None
    settings.default_language = None
    settings.rp2_plugins_json = json.dumps(DEFAULT_RP2_PLUGINS)


def rp2_plugins(settings: AppSettings) -> tuple[str, ...]:
    try:
        configured = json.loads(settings.rp2_plugins_json)
    except (TypeError, json.JSONDecodeError):
        configured = []
    return tuple(dict.fromkeys(str(plugin).strip().lower() for plugin in configured if str(plugin).strip()))


def get_or_create_settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, 1)
    if settings is None:
        settings = AppSettings(id=1)
        session.add(settings)
        session.commit()
    return settings
