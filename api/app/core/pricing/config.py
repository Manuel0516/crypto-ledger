from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import get_or_create_settings
from app.security.secrets import decrypt_config

from .coingecko import CoinGeckoProvider
from .provider import PriceProvider


def configured_price_provider(session: Session) -> PriceProvider:
    """Resolve the selected provider in one place.

    CoinGecko is the only shipped implementation today, but the persisted
    provider key means adding another provider will not require changing the
    ledger, reports, or settings contract.
    """
    settings = get_or_create_settings(session)
    api_key = None
    if settings.price_provider_api_key_encrypted:
        api_key = decrypt_config(settings.price_provider_api_key_encrypted).get("api_key")
    if settings.price_provider == "coingecko":
        return CoinGeckoProvider(timeout=float(settings.price_timeout_seconds), api_key=api_key)
    return CoinGeckoProvider(timeout=float(settings.price_timeout_seconds), api_key=api_key)
