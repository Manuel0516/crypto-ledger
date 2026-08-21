from __future__ import annotations

from app.connectors.binance import BinanceLiveConnector
from app.connectors.bitcoin import BitcoinAddressConnector, BitcoinXpubConnector, looks_like_extended_key
from app.connectors.bitget import BitgetLiveConnector
from app.connectors.evm import EVMAddressConnector
from app.connectors.lightning import LightningConnector
from app.connectors.lightning.nwc import NWCConnector
from app.connectors.monero import MoneroConnector
from app.connectors.solana import SolanaAddressConnector
from app.db.models import Account
from app.security.secrets import decrypt_config

# Split out from sync.py so both sync.py and reconcile.py can build a
# connector without importing each other (sync triggers reconciliation,
# reconcile.py stands alone as its own API endpoint too — importing sync.py
# from reconcile.py, and reconcile.py from sync.py, would be circular).


def build_connector(account: Account):
    if account.connector_type == "bitcoin_address":
        address = account.address or ""
        if looks_like_extended_key(address):
            return BitcoinXpubConnector(address, account.name)
        return BitcoinAddressConnector(address, account.name)
    if account.connector_type == "evm_address":
        return EVMAddressConnector(account.address, account.name, chain=account.chain_network or "ethereum")
    if account.connector_type == "solana_address":
        return SolanaAddressConnector(account.address, account.name)
    if account.connector_type == "monero_rpc":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return MoneroConnector(
            config.get("host", "127.0.0.1"),
            int(config.get("port", 18082)),
            account.name,
            username=config.get("username") or None,
            password=config.get("password") or None,
        )
    if account.connector_type == "lightning_node":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return LightningConnector(config.get("host", ""), config.get("macaroon", ""), account.name)
    if account.connector_type == "lightning_nwc":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return NWCConnector(config.get("connection_string", ""), account.name)
    if account.connector_type == "bitget_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        return BitgetLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), config.get("passphrase", ""), account.name)
    if account.connector_type == "binance_live":
        config = decrypt_config(account.config_encrypted) if account.config_encrypted else {}
        symbols = [s.strip().upper() for s in (config.get("symbols") or "").split(",") if s.strip()]
        return BinanceLiveConnector(config.get("api_key", ""), config.get("api_secret", ""), account.name, symbols=symbols)
    return None
