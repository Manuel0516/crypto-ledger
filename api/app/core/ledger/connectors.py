from __future__ import annotations

from app.connectors.binance import BinanceLiveConnector
from app.connectors.bitcoin import BitcoinAddressConnector, BitcoinXpubConnector, looks_like_extended_key
from app.connectors.bitget import BitgetLiveConnector
from app.connectors.evm import EVMAddressConnector
from app.core.settings import explorer_api_keys
from app.db.models import Account, AppSettings
from app.security.secrets import decrypt_config

# Split out from sync.py so both sync.py and reconcile.py can build a
# connector without importing each other (sync triggers reconciliation,
# reconcile.py stands alone as its own API endpoint too — importing sync.py
# from reconcile.py, and reconcile.py from sync.py, would be circular).


def build_connector(account: Account, session=None):
    if account.connector_type == "bitcoin_address":
        address = account.address or ""
        if looks_like_extended_key(address):
            return BitcoinXpubConnector(address, account.name)
        return BitcoinAddressConnector(address, account.name)
    if account.connector_type == "evm_address":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        # New EVM sources inherit application-wide provider credentials. Keep
        # the old per-account values as explicit overrides so existing
        # connections continue to work after this settings change.
        if session is not None:
            global_keys = explorer_api_keys(session.get(AppSettings, 1))
            config.setdefault("explorer_api_key", global_keys.get("etherscan"))
            if (account.chain_network or "") == "bsc":
                config.setdefault("bsc_trace_api_key", global_keys.get("bsc_trace"))
        return EVMAddressConnector(account.address or "", account.name, chain=account.chain_network or "ethereum", config=config)
    if account.connector_type == "bitget_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return BitgetLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), config.get("passphrase", ""), account.name)
    if account.connector_type == "binance_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        symbols = [s.strip().upper() for s in (config.get("symbols") or "").split(",") if s.strip()]
        return BinanceLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), account.name, symbols=symbols)
    return None
