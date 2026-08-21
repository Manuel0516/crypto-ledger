from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session


@dataclass
class ReadinessIssue:
    """One thing standing between 'we have events' and 'this report is
    trustworthy'. Blocking issues stop generation outright (plan §95:
    'deficiencies must never be hidden'); warnings are shown but don't."""

    severity: str  # "blocking" | "warning"
    title: str
    detail: str
    event_id: int | None = None
    # Set only for "this manual entry could be X" suggestions — the frontend
    # uses it to offer a one-click "Apply" button (a normal event_type
    # override under the hood, never applied automatically).
    suggested_event_type: str | None = None
    # Set only for "this looks like the other leg of an internal transfer"
    # suggestions — the frontend offers a one-click "Link" button (creates a
    # normal INTERNAL_TRANSFER EventLink, never applied automatically).
    suggested_link_event_id: int | None = None
    suggested_link_confidence: str | None = None  # "high" | "medium"


@dataclass
class TaxReadinessResult:
    country: str
    tax_year: int
    event_count: int
    priced_event_count: int
    missing_price_count: int
    manual_event_count: int
    unresolved_issue_count: int
    ambiguous_transfer_count: int
    # Additional dimensions from plan §95's example checklist, beyond price
    # completeness and transfer/manual/review status.
    unsynced_source_count: int = 0
    unpriced_fee_count: int = 0
    missing_raw_evidence_count: int = 0
    # Simplified report-readiness counters. Older fields remain for
    # compatibility with existing clients and report history.
    activity_count: int = 0
    priced_activity_count: int = 0
    warning_count: int = 0
    incomplete_activity_count: int = 0
    issues: list[ReadinessIssue] = field(default_factory=list)
    ready: bool = True


@dataclass
class GainLossRow:
    year: int
    asset: str
    category: str  # SELL | SWAP | DONATE | GIFT | LOST | MOVE (fee-only)
    term: str | None  # "LONG" | "SHORT" | None (only if the country distinguishes)
    quantity: str
    proceeds: str
    cost_basis: str
    gain_loss: str
    event_ids: list[int] = field(default_factory=list)


@dataclass
class IncomeRow:
    year: int
    asset: str
    category: str  # STAKING | MINING | AIRDROP | INTEREST | INCOME | GIFT_RECEIVED | WAGES
    quantity: str
    fiat_value: str
    event_ids: list[int] = field(default_factory=list)


@dataclass
class AssetSummaryRow:
    asset: str
    acquired_quantity: str
    disposed_quantity: str
    held_quantity: str
    cost_basis_remaining: str


@dataclass
class AcquisitionRow:
    year: int
    asset: str
    category: str  # BUY | AIRDROP | GIFT_RECEIVED | STAKING | MINING | INTEREST | ...
    quantity: str
    cost_basis: str
    event_ids: list[int] = field(default_factory=list)


@dataclass
class TransferRow:
    """A confirmed internal transfer (plan §66's "Transfers" section) —
    non-taxable, but still worth showing an accountant so a large balance
    movement isn't mistaken for a disposal that's simply missing."""

    occurred_at: datetime
    asset: str
    quantity: str
    from_label: str
    to_label: str
    event_ids: list[int]


@dataclass
class CorrectionRow:
    """A manual correction applied to an event feeding this report (plan
    §66's "Manual corrections" section) — makes visible what an accountant
    would otherwise only find by comparing raw source data to the report."""

    event_id: int
    occurred_at: datetime
    field: str
    old_value: str
    new_value: str
    changed_at: datetime


@dataclass
class EventScheduleRow:
    """One row of the itemized, per-event listing (plan §66's "Detailed
    event schedule")."""

    event_id: int
    occurred_at: datetime
    event_type: str
    asset: str
    amount: str
    secondary_asset: str | None
    secondary_amount: str | None
    source_wallet: str | None
    destination_wallet: str | None


@dataclass
class ReconciliationSummary:
    """Plan §66's "Reconciliation status" section — a compact readiness
    snapshot at the moment the report was generated."""

    linked_transfer_count: int
    unresolved_issue_count: int
    manual_event_count: int


@dataclass
class TaxCalculationResult:
    country: str
    tax_year: int
    method: str
    currency: str
    generated_at: datetime
    gain_loss_rows: list[GainLossRow]
    income_rows: list[IncomeRow]
    asset_summary: list[AssetSummaryRow]
    total_short_term_gain: str
    total_long_term_gain: str
    total_income: str
    total_fees: str
    warnings: list[str]
    engine: str = "native"  # "rp2" | "native" — mirrors the adapter's own `engine` attribute
    acquisition_rows: list[AcquisitionRow] = field(default_factory=list)
    transfer_rows: list[TransferRow] = field(default_factory=list)
    correction_rows: list[CorrectionRow] = field(default_factory=list)
    event_schedule_rows: list[EventScheduleRow] = field(default_factory=list)
    event_schedule_total: int = 0  # total year-events before the schedule's row cap was applied
    reconciliation: ReconciliationSummary | None = None
    included_activity_count: int = 0
    schedule_only_activity_count: int = 0
    # Extra downloadable files an adapter produces beyond the standard
    # PDF/CSV (e.g. Spain's raw RP2 ODS outputs) — {display_name: file_path}.
    raw_outputs: dict[str, Path] = field(default_factory=dict)


class TaxAdapter(Protocol):
    country_code: str  # ISO 3166-1 alpha-2, e.g. "ES", "SE"
    country_name: str
    default_currency: str
    supported_methods: list[str]
    default_method: str
    engine: str  # "rp2" | "native" — surfaced to the UI, not a behavior switch

    def check_readiness(self, session: Session, tax_year: int) -> TaxReadinessResult: ...

    def calculate(
        self, session: Session, tax_year: int, method: str, taxpayer_name: str, work_dir: Path
    ) -> TaxCalculationResult: ...
