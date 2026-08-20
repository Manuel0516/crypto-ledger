from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from ..adapter import AssetSummaryRow, TaxCalculationResult, TaxReadinessResult
from ..common import build_readiness, build_supplementary_rows, classify_moves, load_events_through
from ..format import format_money, format_quantity


class GeneralAdapter:
    """An audit-ready summary, explicitly not jurisdiction-specific tax advice."""

    country_code = "GENERAL"
    country_name = "General ledger review"
    default_currency = "EUR"
    supported_methods = ["LEDGER_SUMMARY"]
    default_method = "LEDGER_SUMMARY"
    engine = "native"
    version = "tax-general-0.1"

    def check_readiness(self, session: Session, tax_year: int) -> TaxReadinessResult:
        events = load_events_through(session, tax_year)
        _, ambiguous = classify_moves(events)
        return build_readiness(self.country_code, tax_year, session, events, self.default_currency, ambiguous)

    def calculate(self, session: Session, tax_year: int, method: str, taxpayer_name: str, work_dir: Path) -> TaxCalculationResult:
        events = load_events_through(session, tax_year)
        pairs, _ = classify_moves(events)
        balances: dict[str, Decimal] = {}
        for event in events:
            direction = Decimal(-1) if event.direction == "-" else Decimal(1)
            balances[event.primary_asset.symbol] = balances.get(event.primary_asset.symbol, Decimal(0)) + direction * Decimal(event.primary_amount)
        transfers, corrections, schedule, schedule_total, reconciliation = build_supplementary_rows(session, events, pairs, tax_year)
        return TaxCalculationResult(
            country=self.country_code,
            tax_year=tax_year,
            method=self.default_method,
            currency=self.default_currency,
            generated_at=datetime.now(timezone.utc),
            gain_loss_rows=[],
            income_rows=[],
            asset_summary=[AssetSummaryRow(asset=symbol, acquired_quantity="—", disposed_quantity="—", held_quantity=format_quantity(amount), cost_basis_remaining="—") for symbol, amount in sorted(balances.items())],
            total_short_term_gain=format_money(0),
            total_long_term_gain=format_money(0),
            total_income=format_money(0),
            total_fees=format_money(0),
            warnings=["This is a jurisdiction-neutral ledger review, not a tax calculation or filing-ready tax report."],
            engine=self.engine,
            transfer_rows=transfers,
            correction_rows=corrections,
            event_schedule_rows=schedule,
            event_schedule_total=schedule_total,
            reconciliation=reconciliation,
        )
