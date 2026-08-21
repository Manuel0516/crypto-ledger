from __future__ import annotations

import json
import os
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.settings import (
    DEFAULT_VALUATION_CURRENCIES,
    EVIDENCE_RETENTION_POLICY,
    SUPPORTED_PRICE_PROVIDERS,
    THEME_OPTIONS,
    explorer_api_keys,
    get_or_create_settings,
    rp2_plugins,
    reset_settings,
    valuation_currencies,
)
from app.db.models import Account, AppSettings
from app.security.secrets import decrypt_config, encrypt_config

from .deps import get_session

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _serialize(settings: AppSettings) -> dict:
    return {
        "sync_interval_minutes": settings.sync_interval_minutes,
        "sync_enabled": settings.sync_enabled,
        "display_currency": settings.display_currency,
        "minimum_activity_value": float(settings.minimum_activity_value),
        "minimum_activity_currency": settings.minimum_activity_currency,
        "valuation_currencies": list(valuation_currencies(settings)),
        "price_provider": settings.price_provider,
        "price_provider_api_key_configured": bool(settings.price_provider_api_key_encrypted),
        "explorer_api_keys_configured": {name: True for name in explorer_api_keys(settings)},
        "price_timeout_seconds": settings.price_timeout_seconds,
        "backup_hour_utc": settings.backup_hour_utc,
        "backup_verify_after_create": settings.backup_verify_after_create,
        "backup_retention_daily": settings.backup_retention_daily,
        "backup_retention_weekly": settings.backup_retention_weekly,
        "backup_retention_monthly": settings.backup_retention_monthly,
        "ui_theme": settings.ui_theme,
        "default_timezone": settings.default_timezone,
        "evidence_retention_policy": settings.evidence_retention_policy,
        "default_country": settings.default_country,
        "default_tax_year": settings.default_tax_year,
        "taxpayer_name": settings.taxpayer_name,
        "default_language": settings.default_language,
        "rp2_plugins": list(rp2_plugins(settings)),
    }


@router.get("")
def get_settings(session: Session = Depends(get_session)):
    return _serialize(get_or_create_settings(session))


@router.get("/status")
def settings_status(session: Session = Depends(get_session)):
    """Expose configuration health without ever returning a secret or host path."""
    settings = get_or_create_settings(session)
    return {
        "database": "SQLite",
        "price_provider_api_key_configured": bool(settings.price_provider_api_key_encrypted),
        "explorer_api_keys_configured": {name: True for name in explorer_api_keys(settings)},
        "backup_encryption_configured": bool(os.getenv("BACKUP_ENCRYPTION_KEY")),
        "application_secret_configured": bool(os.getenv("APP_SECRET_KEY") or os.getenv("BACKUP_ENCRYPTION_KEY")),
        "evidence_retention": EVIDENCE_RETENTION_POLICY,
        "supported_price_providers": list(SUPPORTED_PRICE_PROVIDERS),
    }


class SettingsUpdate(BaseModel):
    sync_interval_minutes: int | None = Field(default=None, ge=1, le=43200)
    sync_enabled: bool | None = None
    display_currency: str | None = Field(default=None, min_length=3, max_length=3)
    minimum_activity_value: Decimal | None = Field(default=None, ge=0, le=1000000)
    minimum_activity_currency: str | None = Field(default=None, min_length=3, max_length=3)
    valuation_currencies: list[str] | None = Field(default=None, min_length=2, max_length=8)
    price_provider: str | None = None
    price_timeout_seconds: int | None = Field(default=None, ge=3, le=60)
    backup_hour_utc: int | None = Field(default=None, ge=0, le=23)
    backup_verify_after_create: bool | None = None
    backup_retention_daily: int | None = Field(default=None, ge=1, le=365)
    backup_retention_weekly: int | None = Field(default=None, ge=1, le=104)
    backup_retention_monthly: int | None = Field(default=None, ge=1, le=120)
    ui_theme: str | None = None
    default_timezone: str | None = None
    default_country: str | None = None
    default_tax_year: int | None = Field(default=None, ge=2009, le=2100)
    taxpayer_name: str | None = None
    default_language: str | None = None
    rp2_plugins: list[str] | None = Field(default=None, max_length=8)

    @field_validator("display_currency")
    @classmethod
    def validate_display_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else value

    @field_validator("minimum_activity_currency")
    @classmethod
    def validate_minimum_activity_currency(cls, value: str | None) -> str | None:
        value = value.strip().upper() if value else value
        if value and not value.isalpha():
            raise ValueError("Minimum activity currency must use letters only")
        return value

    @field_validator("valuation_currencies")
    @classmethod
    def validate_valuation_currencies(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        currencies = list(dict.fromkeys(currency.strip().upper() for currency in value if currency.strip()))
        if any(len(currency) != 3 or not currency.isalpha() for currency in currencies):
            raise ValueError("Valuation currencies must use three-letter ISO codes")
        missing = set(DEFAULT_VALUATION_CURRENCIES) - set(currencies)
        if missing:
            raise ValueError("EUR and SEK are required for the current ledger and tax adapters")
        return currencies

    @field_validator("price_provider")
    @classmethod
    def validate_price_provider(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_PRICE_PROVIDERS:
            raise ValueError(f"Unsupported price provider: {value}")
        return normalized

    @field_validator("ui_theme")
    @classmethod
    def validate_theme(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in THEME_OPTIONS:
            raise ValueError("Theme must be system, light, or dark")
        return normalized

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Use an IANA timezone, for example Europe/Stockholm") from exc
        return value

    @field_validator("rp2_plugins")
    @classmethod
    def validate_rp2_plugins(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        plugins = list(dict.fromkeys(plugin.strip().lower() for plugin in value if plugin.strip()))
        if any(not plugin.startswith("rp2_") or not plugin.replace("_", "").isalnum() for plugin in plugins):
            raise ValueError("RP2 plug-ins must use a command name such as rp2_es")
        return plugins


@router.patch("")
def update_settings(body: SettingsUpdate, session: Session = Depends(get_session)):
    settings = get_or_create_settings(session)
    if body.sync_interval_minutes is not None:
        settings.sync_interval_minutes = body.sync_interval_minutes
    if body.sync_enabled is not None:
        settings.sync_enabled = body.sync_enabled
    if body.display_currency is not None:
        settings.display_currency = body.display_currency
    if body.minimum_activity_value is not None:
        settings.minimum_activity_value = format(body.minimum_activity_value, "f")
    if body.valuation_currencies is not None:
        settings.valuation_currencies_json = json.dumps(body.valuation_currencies)
        if settings.minimum_activity_currency not in body.valuation_currencies:
            settings.minimum_activity_currency = body.valuation_currencies[0]
    if body.minimum_activity_currency is not None:
        if body.minimum_activity_currency not in valuation_currencies(settings):
            raise HTTPException(422, "Minimum activity currency must be one of the configured valuation currencies")
        settings.minimum_activity_currency = body.minimum_activity_currency
    if body.price_provider is not None:
        settings.price_provider = body.price_provider
    if body.price_timeout_seconds is not None:
        settings.price_timeout_seconds = body.price_timeout_seconds
    if body.backup_hour_utc is not None:
        settings.backup_hour_utc = body.backup_hour_utc
    if body.backup_verify_after_create is not None:
        settings.backup_verify_after_create = body.backup_verify_after_create
    if body.backup_retention_daily is not None:
        settings.backup_retention_daily = body.backup_retention_daily
    if body.backup_retention_weekly is not None:
        settings.backup_retention_weekly = body.backup_retention_weekly
    if body.backup_retention_monthly is not None:
        settings.backup_retention_monthly = body.backup_retention_monthly
    if body.ui_theme is not None:
        settings.ui_theme = body.ui_theme
    if body.default_timezone is not None:
        settings.default_timezone = body.default_timezone
    if body.default_country is not None:
        settings.default_country = body.default_country.upper() or None
    if body.default_tax_year is not None:
        settings.default_tax_year = body.default_tax_year
    if body.taxpayer_name is not None:
        settings.taxpayer_name = body.taxpayer_name.strip() or None
    if body.default_language is not None:
        settings.default_language = body.default_language.lower() or None
    if body.rp2_plugins is not None:
        settings.rp2_plugins_json = json.dumps(body.rp2_plugins)
    session.commit()
    return _serialize(settings)


@router.post("/reset")
def reset(session: Session = Depends(get_session)):
    settings = get_or_create_settings(session)
    reset_settings(settings)
    session.commit()
    return _serialize(settings)


class ProviderKeyInput(BaseModel):
    value: str = Field(min_length=1, max_length=512)


class SecretUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=512)


class SecretConfirmation(BaseModel):
    confirmed: bool = False


_SECRET_FIELDS = {"api_key", "api_secret", "passphrase", "password", "rpc_password", "macaroon"}
_EXPLORER_KEY_NAMES = {"etherscan", "bsc_trace"}


def _secret_inventory(session: Session) -> list[dict]:
    settings = get_or_create_settings(session)
    configured_explorer_keys = explorer_api_keys(settings)
    items = [
        {
            "id": "price-provider-key",
            "label": "CoinGecko API key",
            "location": "Application vault",
            "configured": bool(settings.price_provider_api_key_encrypted),
            "revealable": bool(settings.price_provider_api_key_encrypted),
            "deletable": bool(settings.price_provider_api_key_encrypted),
            "editable": bool(settings.price_provider_api_key_encrypted),
        },
        {
            "id": "explorer:etherscan",
            "label": "Etherscan / BscScan API key",
            "location": "Application vault",
            "configured": bool(configured_explorer_keys.get("etherscan")),
            "revealable": bool(configured_explorer_keys.get("etherscan")),
            "deletable": bool(configured_explorer_keys.get("etherscan")),
            "editable": bool(configured_explorer_keys.get("etherscan")),
        },
        {
            "id": "explorer:bsc_trace",
            "label": "BSCTrace / MegaNode API key",
            "location": "Application vault",
            "configured": bool(configured_explorer_keys.get("bsc_trace")),
            "revealable": bool(configured_explorer_keys.get("bsc_trace")),
            "deletable": bool(configured_explorer_keys.get("bsc_trace")),
            "editable": bool(configured_explorer_keys.get("bsc_trace")),
        },
        {
            "id": "application-master-key",
            "label": "Application encryption key",
            "location": "Host environment",
            "configured": bool(os.getenv("APP_SECRET_KEY") or os.getenv("BACKUP_ENCRYPTION_KEY")),
            "revealable": bool(os.getenv("APP_SECRET_KEY") or os.getenv("BACKUP_ENCRYPTION_KEY")),
            "deletable": False,
            "editable": False,
        },
        {
            "id": "backup-encryption-key",
            "label": "Backup encryption key",
            "location": "Host environment",
            "configured": bool(os.getenv("BACKUP_ENCRYPTION_KEY")),
            "revealable": bool(os.getenv("BACKUP_ENCRYPTION_KEY")),
            "deletable": False,
            "editable": False,
        },
    ]
    for account in session.query(Account).filter(Account.config_encrypted.isnot(None)).order_by(Account.name).all():
        try:
            config = decrypt_config(account.config_encrypted)
        except ValueError:
            continue
        for field, value in config.items():
            if field in _SECRET_FIELDS and value:
                items.append({"id": f"account:{account.id}:{field}", "label": f"{account.name} · {field.replace('_', ' ')}", "location": "Linked account vault", "configured": True, "revealable": True, "deletable": True, "editable": True})
    return items


@router.get("/secrets")
def list_secrets(session: Session = Depends(get_session)):
    return _secret_inventory(session)


@router.post("/secrets/price-provider-key")
def store_provider_key(body: ProviderKeyInput, session: Session = Depends(get_session)):
    settings = get_or_create_settings(session)
    settings.price_provider_api_key_encrypted = encrypt_config({"api_key": body.value.strip()})
    session.commit()
    return {"configured": True}


@router.post("/secrets/explorer/{provider}")
def store_explorer_key(provider: str, body: ProviderKeyInput, session: Session = Depends(get_session)):
    provider = provider.strip().lower()
    if provider not in _EXPLORER_KEY_NAMES:
        raise HTTPException(404, "Unknown explorer provider")
    settings = get_or_create_settings(session)
    keys = explorer_api_keys(settings)
    keys[provider] = body.value.strip()
    settings.explorer_api_keys_encrypted = encrypt_config(keys)
    session.commit()
    return {"provider": provider, "configured": True}


@router.post("/secrets/{secret_id}/reveal")
def reveal_secret(secret_id: str, body: SecretConfirmation, session: Session = Depends(get_session)):
    if not body.confirmed:
        raise HTTPException(400, "Confirm before revealing a secret")
    settings = get_or_create_settings(session)
    if secret_id == "price-provider-key" and settings.price_provider_api_key_encrypted:
        return {"value": decrypt_config(settings.price_provider_api_key_encrypted).get("api_key", "")}
    if secret_id.startswith("explorer:"):
        provider = secret_id.split(":", 1)[1]
        value = explorer_api_keys(settings).get(provider)
        if value:
            return {"value": value}
        raise HTTPException(404, "This explorer key is not configured")
    if secret_id == "application-master-key":
        return {"value": os.getenv("APP_SECRET_KEY") or os.getenv("BACKUP_ENCRYPTION_KEY") or ""}
    if secret_id == "backup-encryption-key":
        return {"value": os.getenv("BACKUP_ENCRYPTION_KEY", "")}
    if secret_id.startswith("account:"):
        _, account_id, field = secret_id.split(":", 2)
        account = session.get(Account, int(account_id))
        if account and account.config_encrypted and field in _SECRET_FIELDS:
            return {"value": decrypt_config(account.config_encrypted).get(field, "")}
    raise HTTPException(404, "This secret is host-managed or no longer available")


@router.patch("/secrets/{secret_id}")
def update_secret(secret_id: str, body: SecretUpdate, session: Session = Depends(get_session)):
    """Replace an app-managed credential; host environment values are read-only here."""
    settings = get_or_create_settings(session)
    if secret_id == "price-provider-key" and settings.price_provider_api_key_encrypted:
        settings.price_provider_api_key_encrypted = encrypt_config({"api_key": body.value.strip()})
    elif secret_id.startswith("explorer:"):
        provider = secret_id.split(":", 1)[1]
        if provider not in _EXPLORER_KEY_NAMES:
            raise HTTPException(404, "Unknown explorer provider")
        keys = explorer_api_keys(settings)
        if provider not in keys:
            raise HTTPException(404, "This explorer key is not configured")
        keys[provider] = body.value.strip()
        settings.explorer_api_keys_encrypted = encrypt_config(keys)
    elif secret_id.startswith("account:"):
        _, account_id, field = secret_id.split(":", 2)
        account = session.get(Account, int(account_id))
        if account is None or not account.config_encrypted or field not in _SECRET_FIELDS:
            raise HTTPException(404, "This secret is host-managed or no longer available")
        config = decrypt_config(account.config_encrypted)
        config[field] = body.value.strip()
        account.config_encrypted = encrypt_config(config)
    else:
        raise HTTPException(400, "Host-managed secrets must be modified in the deployment environment")
    session.commit()
    return {"updated": True}


@router.delete("/secrets/{secret_id}")
def delete_secret(secret_id: str, body: SecretConfirmation, session: Session = Depends(get_session)):
    if not body.confirmed:
        raise HTTPException(400, "Confirm before permanently deleting a secret")
    settings = get_or_create_settings(session)
    if secret_id == "price-provider-key" and settings.price_provider_api_key_encrypted:
        settings.price_provider_api_key_encrypted = None
    elif secret_id.startswith("explorer:"):
        provider = secret_id.split(":", 1)[1]
        keys = explorer_api_keys(settings)
        if provider not in keys:
            raise HTTPException(404, "This explorer key is not configured")
        keys.pop(provider, None)
        settings.explorer_api_keys_encrypted = encrypt_config(keys) if keys else None
    elif secret_id.startswith("account:"):
        _, account_id, field = secret_id.split(":", 2)
        account = session.get(Account, int(account_id))
        if account is None or not account.config_encrypted or field not in _SECRET_FIELDS:
            raise HTTPException(404, "Secret not found")
        config = decrypt_config(account.config_encrypted)
        config.pop(field, None)
        account.config_encrypted = encrypt_config(config) if config else None
    else:
        raise HTTPException(400, "Host-managed secrets must be removed from the deployment environment")
    session.commit()
    return {"deleted": True}
