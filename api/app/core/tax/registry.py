from __future__ import annotations

from .adapter import TaxAdapter
from .es.adapter import SpainAdapter
from .general.adapter import GeneralAdapter
from .se.adapter import SwedenAdapter

ADAPTERS: dict[str, TaxAdapter] = {
    "GENERAL": GeneralAdapter(),
    "ES": SpainAdapter(),
    "SE": SwedenAdapter(),
}


def get_adapter(country_code: str) -> TaxAdapter:
    adapter = ADAPTERS.get(country_code.upper())
    if adapter is None:
        raise ValueError(f"No tax adapter available for country '{country_code}'")
    return adapter


def list_countries() -> list[dict]:
    return [
        {
            "code": adapter.country_code,
            "name": adapter.country_name,
            "currency": adapter.default_currency,
            "methods": adapter.supported_methods,
            "default_method": adapter.default_method,
            "engine": adapter.engine,
        }
        for adapter in ADAPTERS.values()
    ]
