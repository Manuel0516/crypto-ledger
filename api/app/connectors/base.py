from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class RawRecord:
    """A source record exactly as received, before any interpretation."""

    source_id: str
    external_id: str
    source_timestamp: datetime | None
    payload: dict[str, Any]
    source_timezone: str | None = None
    source_reference: str | None = None


@dataclass(frozen=True)
class NormalizedFee:
    fee_type: str
    asset_symbol: str
    amount: str
    fee_recipient: str | None = None  # who/what received the fee, where known (plan §19)


@dataclass(frozen=True)
class NormalizedEvent:
    """What a connector's normalizer produces from one RawRecord. Tax-neutral."""

    event_type: str
    event_subtype: str | None
    direction: str  # "+" | "-"
    status: str
    occurred_at: datetime
    original_timestamp: str | None
    asset_symbol: str
    amount: str
    account_name: str
    notes: str | None = None
    # The activity's from/to wallet values. These normally contain raw
    # addresses, but a connector may provide a registered wallet name when no
    # public address exists.
    address_from: str | None = None
    address_to: str | None = None
    fees: list[NormalizedFee] = field(default_factory=list)
    asset_network: str | None = None  # disambiguates tokens sharing a symbol (plan §25)
    asset_contract: str | None = None
    asset_type: str | None = None  # COIN | TOKEN | STABLECOIN | NFT | ... (plan §25); default COIN if unset

    # A second economic leg, e.g. a swap's received side (plan §15: "BTC
    # -0.001 / ETH +0.034"). amount is always positive; direction is implied
    # to be opposite of the primary leg's direction.
    secondary_asset_symbol: str | None = None
    secondary_asset_network: str | None = None
    secondary_amount: str | None = None

    # Structured network/exchange evidence, "where applicable" (plan §17-18).
    tx_hash: str | None = None
    block_height: int | None = None
    block_hash: str | None = None
    log_index: int | None = None
    contract_address: str | None = None  # the contract called, if any
    order_id: str | None = None
    trade_id: str | None = None
    deposit_id: str | None = None
    withdrawal_id: str | None = None
    # Human/audit metadata. Connectors can populate these where a source
    # provides them; manual activity uses the same fields.
    description: str | None = None
    merchant: str | None = None
    tags: list[str] = field(default_factory=list)
    evidence_reference: str | None = None
    source_timezone: str | None = None


@dataclass(frozen=True)
class Balance:
    """A live "how much do I actually have right now" snapshot for one
    asset, straight from the source (an exchange balance endpoint, an
    address's on-chain balance) — independent of the event ledger. Used only
    for reconciliation: comparing this against the ledger-computed holding
    for the same asset surfaces drift (a gap in event coverage, a
    normalization bug) without treating the live number as the ledger
    itself. Never used to fabricate history."""

    asset_symbol: str
    amount: str
    asset_network: str | None = None
    asset_contract: str | None = None
    asset_type: str | None = None  # COIN | TOKEN | STABLECOIN | ...


class Connector(Protocol):
    source_id: str
    version: str

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]: ...

    def normalize(self, raw: RawRecord) -> NormalizedEvent: ...

    # fetch_balances(self) -> Iterable[Balance] is deliberately NOT part of
    # this Protocol: it's optional, source-by-source. Callers check for it
    # with getattr(connector, "fetch_balances", None) — a source with no
    # live balance concept, or one this app hasn't wired up yet, simply
    # doesn't offer a reconciliation check.


class ConnectorUnavailable(Exception):
    """Raised when a connector's external endpoint can't be reached right now
    (a node/daemon isn't running, a public API is down). Never a reason to
    lose or fabricate ledger data — callers turn this into a status + Issue."""
