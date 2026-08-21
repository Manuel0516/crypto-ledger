from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """A financial ownership boundary: an exchange, wallet, or manual bucket."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # exchange | wallet | manual | other
    connector_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    chain_network: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    config_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="not_configured")
    wallet_software: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Paused: a user-requested pause on an otherwise-connected source — it
    # stays visible (unlike archive) but the scheduler skips it, and manual
    # Sync/Backfill are refused too (plan §89's action list is Sync /
    # Backfill / Edit / Disable / Archive — five distinct actions).
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set whenever this account's connector successfully completes a live
    # balance check (fetch_balances()), even if the result is an empty list
    # (a real zero balance) — distinct from "never checked" (None), so
    # Overview can tell "confirmed empty" apart from "no live data yet,
    # fall back to the computed ledger total" (see AccountBalance).
    balance_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Asset(Base):
    """Never identified by ticker alone — network + contract disambiguate tokens."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False, default="COIN")
    network: Mapped[str | None] = mapped_column(String)
    contract_address: Mapped[str | None] = mapped_column(String)
    coingecko_id: Mapped[str | None] = mapped_column(String)
    decimals: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_assets_network", "network"),
        UniqueConstraint("symbol", "network", "contract_address", name="uq_asset_symbol_network_contract"),
    )


class AccountBalance(Base):
    """Latest live balance snapshot for one (account, asset) pair, from fetch_balances().

    Overwritten in place on every successful balance check — this is a
    current-state snapshot, not a history, so it stays outside the
    append-only raw-evidence/event chain. Overview sums these (when present)
    instead of the computed-from-events ledger total, per-account, so a
    connected exchange/wallet shows what it actually reports right now
    rather than a total that can drift from missed or misclassified events.
    """

    __tablename__ = "account_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    # Smallest-unit-precision decimal string, same convention as Event.primary_amount.
    amount: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    asset: Mapped[Asset] = relationship(foreign_keys=[asset_id])

    __table_args__ = (
        UniqueConstraint("account_id", "asset_id", name="uq_account_balance_account_asset"),
        Index("ix_account_balances_account", "account_id"),
    )


class RawEvent(Base):
    """Immutable evidence as received from a source. Never modified after insert."""

    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The source's stated timezone is evidence, not a display preference. It
    # matters when a CSV gives a local timestamp without an offset.
    source_timezone: Mapped[str | None] = mapped_column(String)
    source_reference: Mapped[str | None] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    connector_version: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_raw_event_source_external"),)


class Event(Base):
    """The canonical, tax-neutral fact. Interpretation happens later, elsewhere."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    raw_event_id: Mapped[int | None] = mapped_column(ForeignKey("raw_events.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    event_subtype: Mapped[str | None] = mapped_column(String)
    direction: Mapped[str | None] = mapped_column(String)  # "+" | "-"
    status: Mapped[str] = mapped_column(String, nullable=False, default="COMPLETE")

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_timestamp: Mapped[str | None] = mapped_column(String)
    source_timezone: Mapped[str | None] = mapped_column(String)

    primary_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    primary_amount: Mapped[str] = mapped_column(String, nullable=False)
    secondary_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    secondary_amount: Mapped[str | None] = mapped_column(String)

    source_label: Mapped[str] = mapped_column(String, nullable=False)
    destination_label: Mapped[str | None] = mapped_column(String)
    counterparty: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    merchant: Mapped[str | None] = mapped_column(String)
    # JSON array, deliberately stored as text so the SQLite database remains
    # portable. The API normalizes and validates it as a list of strings.
    tags_json: Mapped[str | None] = mapped_column(Text)
    evidence_reference: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)

    # Raw addresses (plan §17), distinct from source_label/destination_label
    # which name *accounts*, not addresses. A staking deposit legitimately
    # has only one side populated.
    address_from: Mapped[str | None] = mapped_column(String)
    address_to: Mapped[str | None] = mapped_column(String)

    # Structured network/exchange evidence (plan §17-18) — "where applicable"
    # per source type, so every field here is nullable. Previously this was
    # only ever readable inside RawEvent.payload_json; having it queryable
    # on the event itself is what lets reconciliation match by tx_hash
    # instead of amount+time heuristics alone, and lets exports show a real
    # tx hash / order id column.
    tx_hash: Mapped[str | None] = mapped_column(String)
    block_height: Mapped[int | None] = mapped_column(Integer)
    block_hash: Mapped[str | None] = mapped_column(String)
    log_index: Mapped[int | None] = mapped_column(Integer)
    contract_address: Mapped[str | None] = mapped_column(String)  # the contract called, if any (distinct from the asset's own contract)
    order_id: Mapped[str | None] = mapped_column(String)
    trade_id: Mapped[str | None] = mapped_column(String)
    deposit_id: Mapped[str | None] = mapped_column(String)
    withdrawal_id: Mapped[str | None] = mapped_column(String)

    provenance: Mapped[str] = mapped_column(String, nullable=False, default="automatic")  # automatic | manual
    normalizer_version: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    primary_asset: Mapped[Asset] = relationship(foreign_keys=[primary_asset_id])
    secondary_asset: Mapped["Asset | None"] = relationship(foreign_keys=[secondary_asset_id])
    raw_event: Mapped["RawEvent | None"] = relationship(foreign_keys=[raw_event_id])
    fees: Mapped[list["Fee"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    valuations: Mapped[list["Valuation"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    overrides: Mapped[list["Override"]] = relationship(back_populates="event", cascade="all, delete-orphan")
    outgoing_links: Mapped[list["EventLink"]] = relationship(
        foreign_keys="EventLink.event_id", back_populates="event", cascade="all, delete-orphan"
    )
    incoming_links: Mapped[list["EventLink"]] = relationship(
        foreign_keys="EventLink.linked_event_id", back_populates="linked_event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_asset_occurred_at", "primary_asset_id", "occurred_at"),
        Index("ix_events_account_occurred_at", "account_id", "occurred_at"),
        Index("ix_events_type_occurred_at", "event_type", "occurred_at"),
        Index("ix_events_provenance_occurred_at", "provenance", "occurred_at"),
        Index("ix_events_status_occurred_at", "status", "occurred_at"),
    )


class EventLink(Base):
    """Auditable relation between canonical events.

    Links are directional only for storage; the API presents both incoming and
    outgoing links to either event. This avoids duplicate rows while still
    allowing relationships such as BRIDGE_SOURCE / BRIDGE_DESTINATION to carry
    meaning.
    """

    __tablename__ = "event_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    linked_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False, default="RELATED")
    provenance: Mapped[str] = mapped_column(String, nullable=False, default="manual")  # automatic | manual
    confidence: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[Event] = relationship(foreign_keys=[event_id], back_populates="outgoing_links")
    linked_event: Mapped[Event] = relationship(foreign_keys=[linked_event_id], back_populates="incoming_links")

    __table_args__ = (
        UniqueConstraint("event_id", "linked_event_id", "relationship_type", name="uq_event_link_pair_type"),
        Index("ix_event_links_linked_event_id", "linked_event_id"),
    )


class Fee(Base):
    """A single event may carry more than one fee (exchange fee + network fee, etc.)."""

    __tablename__ = "fees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    fee_type: Mapped[str] = mapped_column(String, nullable=False, default="NETWORK_FEE")
    fee_asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), nullable=False)
    fee_amount: Mapped[str] = mapped_column(String, nullable=False)
    fee_recipient: Mapped[str | None] = mapped_column(String)
    # True for a fee the user added/edited by hand rather than one a
    # connector observed — lets the UI distinguish "evidence" from "correction".
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    event: Mapped[Event] = relationship(back_populates="fees")
    fee_asset: Mapped[Asset] = relationship(foreign_keys=[fee_asset_id])


class Valuation(Base):
    """Historical EUR/SEK value of an event, with full provenance of how it was priced."""

    __tablename__ = "valuations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String, nullable=False)
    unit_price: Mapped[str] = mapped_column(String, nullable=False)
    total_value: Mapped[str] = mapped_column(String, nullable=False)
    requested_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_asset_id: Mapped[str] = mapped_column(String, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)  # EXACT_EXECUTION | DAILY_REFERENCE | MANUAL | ...
    granularity: Mapped[str] = mapped_column(String, nullable=False, default="day")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confidence: Mapped[str] = mapped_column(String, nullable=False, default="medium")
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)

    event: Mapped[Event] = relationship(back_populates="valuations")


class PriceObservation(Base):
    """Local cache of provider price lookups so the same historical price is never re-fetched."""

    __tablename__ = "price_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_asset_id: Mapped[str] = mapped_column(String, nullable=False)
    quote_currency: Mapped[str] = mapped_column(String, nullable=False)
    observation_date: Mapped[str] = mapped_column(String, nullable=False)  # YYYY-MM-DD, provider's granularity
    observation_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unit_price: Mapped[str] = mapped_column(String, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    granularity: Mapped[str] = mapped_column(String, nullable=False, default="day")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_asset_id", "quote_currency", "observation_date", name="uq_price_observation"
        ),
    )


class Override(Base):
    """A user correction to an imported event. The base event row is never rewritten."""

    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    field: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str | None] = mapped_column(Text)

    event: Mapped[Event] = relationship(back_populates="overrides")


class Issue(Base):
    """Uncertainty becomes an explicit, resolvable issue instead of a silent guess."""

    __tablename__ = "issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    severity: Mapped[str] = mapped_column(String, nullable=False, default="warning")
    title: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncState(Base):
    __tablename__ = "sync_state"

    source_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, nullable=False, default="ok")
    records_imported: Mapped[int] = mapped_column(Integer, default=0)


class AppSettings(Base):
    """Singleton row (id always 1) for app-wide configuration."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_currency: Mapped[str] = mapped_column(String, nullable=False, default="EUR")
    valuation_currencies_json: Mapped[str] = mapped_column(Text, nullable=False, default='["EUR", "SEK"]')
    price_provider: Mapped[str] = mapped_column(String, nullable=False, default="coingecko")
    price_provider_api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    price_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    backup_hour_utc: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    backup_verify_after_create: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    backup_retention_daily: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    backup_retention_weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    backup_retention_monthly: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    ui_theme: Mapped[str] = mapped_column(String, nullable=False, default="system")
    default_timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    evidence_retention_policy: Mapped[str] = mapped_column(String, nullable=False, default="indefinite")
    # Minimal tax-report context (plan §57) — not the full Settings page
    # rework, just what the Reports page's country/year picker needs to
    # persist. taxpayer_name doubles as RP2's required "holder" name.
    default_country: Mapped[str | None] = mapped_column(String)
    default_tax_year: Mapped[int | None] = mapped_column(Integer)
    taxpayer_name: Mapped[str | None] = mapped_column(String)
    default_language: Mapped[str | None] = mapped_column(String)
    rp2_plugins_json: Mapped[str] = mapped_column(Text, nullable=False, default='["rp2_es"]')


class TaxReport(Base):
    """A generated tax report — reproducible (plan §96): the inputs that
    produced it are captured alongside the result, not just the result."""

    __tablename__ = "tax_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String, nullable=False)  # ISO 3166-1 alpha-2, e.g. "ES", "SE"
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "FIFO", "AVERAGE_COST"
    language: Mapped[str] = mapped_column(String, nullable=False, default="en")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    adapter_version: Mapped[str] = mapped_column(String, nullable=False)
    # SHA-256 of the full ledger CSV at generation time — lets a later run be
    # compared against this one to see whether the underlying facts changed.
    ledger_snapshot_hash: Mapped[str] = mapped_column(String, nullable=False)
    # SHA-256 of every cached PriceObservation row at generation time — the
    # other half of reproducibility (plan §96 "price dataset"): the ledger
    # hash alone doesn't capture a later price correction/backfill.
    price_dataset_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    # {"report.pdf": sha256, "report.csv": sha256, ...} — lets a downloaded
    # file be verified byte-for-byte against what was originally generated.
    output_hashes_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String, nullable=False, default="complete")  # complete | failed
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # Directory (relative to DATA_DIR) holding the generated PDF/CSV/RP2 files.
    output_dir: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (Index("ix_tax_reports_country_year", "country", "tax_year"),)


class Attachment(Base):
    """A supporting document (plan §69): receipt, invoice, exchange
    statement, staking statement, payment confirmation, etc. Content is
    encrypted at rest; only metadata and a content hash live in SQLite."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    kind: Mapped[str] = mapped_column(String, nullable=False, default="other")
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)  # hash of the plaintext content
    storage_path: Mapped[str] = mapped_column(String, nullable=False)  # encrypted file on disk
    description: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped["Event | None"] = relationship(foreign_keys=[event_id])

    __table_args__ = (Index("ix_attachments_event_id", "event_id"),)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    path: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
