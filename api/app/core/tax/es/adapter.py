from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from ..adapter import AcquisitionRow, AssetSummaryRow, GainLossRow, IncomeRow, TaxCalculationResult, TaxReadinessResult
from ..common import build_readiness, build_supplementary_rows, classify_moves, load_events_through, uncovered_type_warning
from ..format import format_money, format_quantity
from ..rp2_runner import ods_io, runner

_TERM_MAP = {"LARGO": "LONG", "CORTO": "SHORT"}
# RP2's own taxable-event categories (README) split between disposals
# (feed GainLossRow) and acquisitions/income (feed IncomeRow) — MOVE is the
# fee-for-an-internal-transfer category, which is a real disposal in RP2's
# model even though the transfer itself isn't.
_DISPOSAL_CATEGORIES = {"SELL", "DONATE", "GIFT", "LOST", "MOVE"}
_INCOME_CATEGORIES = {"AIRDROP", "INCOME", "INTEREST", "MINING", "STAKING", "WAGES", "HARDFORK"}
# BUY establishes cost basis rather than triggering a gain/loss or income
# event, but plan §66 still wants it shown as its own "Acquisitions" section.
_ACQUISITION_CATEGORIES = {"BUY"}


class SpainAdapter:
    country_code = "ES"
    country_name = "Spain"
    default_currency = "EUR"
    supported_methods = ["FIFO"]
    default_method = "FIFO"
    engine = "rp2"
    version = "tax-es-rp2-0.1"

    def check_readiness(self, session: Session, tax_year: int) -> TaxReadinessResult:
        events = load_events_through(session, tax_year)
        _, ambiguous = classify_moves(events)
        return build_readiness(self.country_code, tax_year, session, events, self.default_currency, ambiguous)

    def calculate(
        self, session: Session, tax_year: int, method: str, taxpayer_name: str, work_dir: Path
    ) -> TaxCalculationResult:
        events = load_events_through(session, tax_year)
        pairs, ambiguous = classify_moves(events)
        if ambiguous:
            raise ValueError(
                f"{len(ambiguous)} transfer(s) aren't linked or classified — resolve the readiness issues first"
            )

        ods_path, ini_path, input_warnings = ods_io.build_input(session, events, pairs, taxpayer_name or "Taxpayer", work_dir)
        output_dir = work_dir / "rp2_output"
        method_arg = method.lower() if method else self.default_method.lower()
        full_report = runner.run("rp2_es", ini_path, ods_path, output_dir, method_arg)
        raw_rows = ods_io.parse_output(full_report)

        gain_rows: list[GainLossRow] = []
        income_rows: list[IncomeRow] = []
        acquisition_rows: list[AcquisitionRow] = []
        for row in raw_rows:
            if row["year"] != tax_year:
                continue
            category = str(row["transaction_type"] or "")
            # RP2's raw output cells are Python floats — round to fixed
            # precision immediately, before they get anywhere near a table
            # cell or CSV column (a repeating-decimal FIFO fraction or a
            # small quantity like 0.00001 otherwise renders as a wall of
            # digits or '1e-05').
            if category in _DISPOSAL_CATEGORIES:
                gain_rows.append(
                    GainLossRow(
                        year=row["year"],
                        asset=row["asset"],
                        category=category,
                        term=_TERM_MAP.get(str(row["term"] or "").upper(), row["term"]),
                        quantity=format_quantity(row["taxable_crypto"] or 0),
                        proceeds=format_money(row["taxable_fiat"] or 0),
                        cost_basis=format_money(row["cost_basis"] or 0),
                        gain_loss=format_money(row["gain"] or 0),
                    )
                )
            elif category in _INCOME_CATEGORIES:
                income_rows.append(
                    IncomeRow(
                        year=row["year"],
                        asset=row["asset"],
                        category=category,
                        quantity=format_quantity(row["taxable_crypto"] or 0),
                        fiat_value=format_money(row["taxable_fiat"] or 0),
                    )
                )
            elif category in _ACQUISITION_CATEGORIES:
                acquisition_rows.append(
                    AcquisitionRow(
                        year=row["year"],
                        asset=row["asset"],
                        category=category,
                        quantity=format_quantity(row["taxable_crypto"] or 0),
                        cost_basis=format_money(row["taxable_fiat"] or 0),
                    )
                )

        total_short = format_money(sum((Decimal(r.gain_loss) for r in gain_rows if r.term == "SHORT"), Decimal(0)))
        total_long = format_money(sum((Decimal(r.gain_loss) for r in gain_rows if r.term == "LONG"), Decimal(0)))
        total_income = format_money(sum((Decimal(r.fiat_value) for r in income_rows), Decimal(0)))

        # Per-asset holding summary for the "Assets" report section — derived
        # from the full (not just tax_year) row set so it reflects what's
        # actually left in each position going into next year.
        acquired: dict[str, Decimal] = {}
        disposed: dict[str, Decimal] = {}
        for row in raw_rows:
            asset = row["asset"]
            qty = Decimal(str(row["taxable_crypto"] or 0))
            if str(row["transaction_type"]) in _DISPOSAL_CATEGORIES:
                disposed[asset] = disposed.get(asset, Decimal(0)) + qty
        asset_summary = [
            AssetSummaryRow(asset=asset, acquired_quantity="-", disposed_quantity=format_quantity(qty), held_quantity="-", cost_basis_remaining="-")
            for asset, qty in sorted(disposed.items())
        ]

        warnings = list(input_warnings)
        skip_ids = {e.id for pair in pairs for e in (pair.withdrawal, pair.deposit)}
        year_events = [e for e in events if e.occurred_at.year == tax_year]
        covered_types = set(ods_io.IN_TYPE_MAP) | set(ods_io.OUT_TYPE_MAP) | {"SWAP"}
        uncovered = uncovered_type_warning(year_events, covered_types, skip_ids)
        if uncovered:
            warnings.append(uncovered)
        warnings.append(
            "Spain support uses RP2's FIFO engine — RP2 offers no guarantee of correctness; verify results with a "
            "tax professional before filing, and check the raw RP2 output files for the full computation detail."
        )

        raw_outputs = {"RP2 full report (ODS)": full_report}
        open_positions = next(output_dir.glob("*open_positions*.ods"), None)
        if open_positions:
            raw_outputs["RP2 open positions (ODS)"] = open_positions

        transfer_rows, correction_rows, event_schedule_rows, event_schedule_total, reconciliation = build_supplementary_rows(
            session, events, pairs, tax_year
        )

        return TaxCalculationResult(
            country=self.country_code,
            tax_year=tax_year,
            method=method_arg.upper(),
            currency=self.default_currency,
            generated_at=datetime.now(timezone.utc),
            gain_loss_rows=gain_rows,
            income_rows=income_rows,
            asset_summary=asset_summary,
            total_short_term_gain=total_short,
            total_long_term_gain=total_long,
            total_income=total_income,
            total_fees=format_money(0),  # fees are folded into cost_basis/proceeds by RP2 itself, not tracked separately here
            warnings=warnings,
            engine=self.engine,
            acquisition_rows=acquisition_rows,
            transfer_rows=transfer_rows,
            correction_rows=correction_rows,
            event_schedule_rows=event_schedule_rows,
            event_schedule_total=event_schedule_total,
            reconciliation=reconciliation,
            raw_outputs=raw_outputs,
        )
