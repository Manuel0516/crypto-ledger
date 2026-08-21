from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Asset

# Seed identities for the symbols the MVP actually encounters. A real asset
# registry would resolve tokens by network + contract address; this table
# covers native chain assets, fiat quote currencies, and a few common
# stablecoins. Anything else (an arbitrary ERC-20, say) is registered on the
# fly from whatever the connector observed — never identified by ticker alone
# once a network/contract is known (plan §25).
KNOWN_ASSETS: dict[str, dict[str, str | int | None]] = {
    "BTC": {"name": "Bitcoin", "asset_type": "COIN", "network": "Bitcoin", "coingecko_id": "bitcoin", "decimals": 8},
    "EUR": {"name": "Euro", "asset_type": "FIAT", "network": None, "coingecko_id": "eur", "decimals": 2},
    "SEK": {"name": "Swedish krona", "asset_type": "FIAT", "network": None, "coingecko_id": "sek", "decimals": 2},
    "ETH": {"name": "Ethereum", "asset_type": "COIN", "network": "Ethereum", "coingecko_id": "ethereum", "decimals": 18},
    "SOL": {"name": "Solana", "asset_type": "COIN", "network": "Solana", "coingecko_id": "solana", "decimals": 9},
    "MATIC": {"name": "Polygon", "asset_type": "COIN", "network": "Polygon", "coingecko_id": "matic-network", "decimals": 18},
    "BNB": {"name": "BNB", "asset_type": "COIN", "network": "BNB Smart Chain", "coingecko_id": "binancecoin", "decimals": 18},
    "XMR": {"name": "Monero", "asset_type": "COIN", "network": "Monero", "coingecko_id": "monero", "decimals": 12},
    "USDC": {"name": "USD Coin", "asset_type": "STABLECOIN", "network": "Ethereum", "coingecko_id": "usd-coin", "decimals": 6},
    "USDT": {"name": "Tether", "asset_type": "STABLECOIN", "network": "Ethereum", "coingecko_id": "tether", "decimals": 6},
    "UNI": {"name": "Uniswap", "asset_type": "COIN", "network": "Ethereum", "coingecko_id": "uniswap", "decimals": 18},
}


def resolve_network(symbol: str, network: str | None, contract_address: str | None = None) -> str | None:
    """The same network-defaulting get_or_create_asset applies, without
    creating anything. An explicit network (or any contract address) always
    wins; otherwise a known native-chain symbol (BTC, ETH, ...) resolves to
    its canonical network even when the source didn't specify one — an
    exchange balance never carries a network, but the ledger's BTC deposit
    from that same exchange still got tagged "Bitcoin" when it was ingested.
    Used to key-match a live balance to the ledger asset it actually is."""
    if network is not None or contract_address is not None:
        return network
    return KNOWN_ASSETS.get(symbol.upper(), {}).get("network")


def get_or_create_asset(
    session: Session,
    symbol: str,
    network: str | None = None,
    *,
    name: str | None = None,
    asset_type: str | None = None,
    contract_address: str | None = None,
    decimals: int | None = None,
) -> Asset:
    symbol = symbol.upper()
    meta = KNOWN_ASSETS.get(symbol, {}) if contract_address is None else {}
    resolved_network = network if network is not None else meta.get("network")

    query = session.query(Asset).filter(Asset.symbol == symbol)
    query = query.filter(Asset.network.is_(None)) if resolved_network is None else query.filter(Asset.network == resolved_network)
    query = query.filter(Asset.contract_address.is_(None)) if contract_address is None else query.filter(Asset.contract_address == contract_address)
    existing = query.one_or_none()
    if existing:
        return existing

    asset = Asset(
        symbol=symbol,
        name=name or meta.get("name", symbol),
        asset_type=asset_type or meta.get("asset_type", "COIN"),
        network=resolved_network,
        contract_address=contract_address,
        coingecko_id=meta.get("coingecko_id"),
        decimals=decimals if decimals is not None else meta.get("decimals"),
    )
    session.add(asset)
    session.flush()
    return asset
