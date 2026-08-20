from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.pricing.cache import get_historical_prices
from app.core.pricing.config import configured_price_provider
from ..common import EffectiveEvent

from ..adapter import AcquisitionRow, AssetSummaryRow, GainLossRow, IncomeRow, TaxCalculationResult, TaxReadinessResult
from ..common import build_readiness, build_supplementary_rows, classify_moves, load_events_through, uncovered_type_warning
from ..format import format_money, format_quantity

# Acquisitions: establish or add to the running average-cost position.
_IN_TYPES = {
    "BUY", "AIRDROP", "INCOME", "INTEREST", "YIELD", "STAKING_REWARD", "MINING_REWARD",
    "GIFT_RECEIVED", "REFERRAL_REWARD", "CASHBACK", "LENDING_REWARD", "LP_REWARD",
    "MARGIN_INTEREST", "NFT_MINT", "TOKEN_MINT", "LIGHTNING_RECEIVE",
}
# Which of those are *also* separately-reportable income (not just a cost-basis event).
_INCOME_CATEGORY = {
    "STAKING_REWARD": "STAKING", "MINING_REWARD": "MINING", "AIRDROP": "AIRDROP",
    "INTEREST": "INTEREST", "YIELD": "INTEREST", "INCOME": "INCOME", "REFERRAL_REWARD": "INCOME",
    "CASHBACK": "INCOME", "LENDING_REWARD": "INTEREST", "LP_REWARD": "INCOME", "MARGIN_INTEREST": "INTEREST",
}
# Disposals: reduce the running position, realize gain/loss against its average cost.
_OUT_TYPES = {"SELL", "PAYMENT", "DONATION", "GIFT_SENT", "LOST", "STOLEN", "NFT_SELL", "LIGHTNING_SEND", "TOKEN_BURN"}


class _Position:
    """A running weighted-average cost basis for one asset (genomsnittsmetoden
    — Sweden's mandatory method: portfolio-wide, not per-wallet or per-lot)."""

    __slots__ = ("quantity", "cost")

    def __init__(self) -> None:
        self.quantity = Decimal(0)
        self.cost = Decimal(0)

    @property
    def unit_cost(self) -> Decimal:
        return self.cost / self.quantity if self.quantity else Decimal(0)

    def acquire(self, quantity: Decimal, cost: Decimal) -> None:
        self.quantity += quantity
        self.cost += cost

    def dispose(self, quantity: Decimal) -> Decimal:
        """Returns the cost basis consumed; reduces the position proportionally.
        Clamped so a data gap (disposing more than recorded as acquired)
        never drives the position negative — it's flagged as a warning by the
        caller instead of silently producing a nonsensical basis."""
        if self.quantity <= 0:
            return Decimal(0)
        consumed = min(quantity, self.quantity)
        basis = self.unit_cost * consumed
        self.quantity -= consumed
        self.cost -= basis
        return basis


def _sek_value(event: EffectiveEvent) -> Decimal:
    for valuation in event.valuations:
        if valuation.quote_currency == "SEK":
            return Decimal(valuation.total_value)
    return Decimal(0)


class SwedenAdapter:
    country_code = "SE"
    country_name = "Sweden"
    default_currency = "SEK"
    supported_methods = ["AVERAGE_COST"]
    default_method = "AVERAGE_COST"
    engine = "native"
    version = "tax-se-0.1"

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
        skip_ids = {e.id for pair in pairs for e in (pair.withdrawal, pair.deposit)}

        positions: dict[str, _Position] = {}
        gain_rows: list[GainLossRow] = []
        income_rows: list[IncomeRow] = []
        acquisition_rows: list[AcquisitionRow] = []
        warnings: list[str] = []
        underflow_assets: set[str] = set()
        acquired: dict[str, Decimal] = {}
        disposed: dict[str, Decimal] = {}

        for event in events:
            if event.id in skip_ids:
                continue  # internal transfer: moving wallets isn't a disposal under Swedish rules
            asset = event.primary_asset.symbol
            pos = positions.setdefault(asset, _Position())
            amount = Decimal(event.primary_amount)
            year = event.occurred_at.year

            if event.event_type in _IN_TYPES:
                value = _sek_value(event)
                pos.acquire(amount, value)
                acquired[asset] = acquired.get(asset, Decimal(0)) + amount
                category = _INCOME_CATEGORY.get(event.event_type)
                if category and year == tax_year:
                    income_rows.append(
                        IncomeRow(year=year, asset=asset, category=category, quantity=format_quantity(amount), fiat_value=format_money(value), event_ids=[event.id])
                    )
                if year == tax_year:
                    acquisition_rows.append(
                        AcquisitionRow(year=year, asset=asset, category=event.event_type, quantity=format_quantity(amount), cost_basis=format_money(value), event_ids=[event.id])
                    )
            elif event.event_type == "SWAP":
                proceeds = _sek_value(event)
                if pos.quantity < amount:
                    underflow_assets.add(asset)
                basis = pos.dispose(amount)
                disposed[asset] = disposed.get(asset, Decimal(0)) + amount
                if year == tax_year:
                    gain_rows.append(GainLossRow(year=year, asset=asset, category="SWAP", term=None, quantity=format_quantity(amount), proceeds=format_money(proceeds), cost_basis=format_money(basis), gain_loss=format_money(proceeds - basis), event_ids=[event.id]))
                if event.secondary_asset and event.secondary_amount:
                    sec_asset = event.secondary_asset.symbol
                    sec_pos = positions.setdefault(sec_asset, _Position())
                    sec_pos.acquire(Decimal(event.secondary_amount), proceeds)
                    acquired[sec_asset] = acquired.get(sec_asset, Decimal(0)) + Decimal(event.secondary_amount)
            elif event.event_type in _OUT_TYPES:
                proceeds = Decimal(0) if event.event_type in ("LOST", "STOLEN") else _sek_value(event)
                if pos.quantity < amount:
                    underflow_assets.add(asset)
                basis = pos.dispose(amount)
                disposed[asset] = disposed.get(asset, Decimal(0)) + amount
                if year == tax_year:
                    gain_rows.append(GainLossRow(year=year, asset=asset, category=event.event_type, term=None, quantity=format_quantity(amount), proceeds=format_money(proceeds), cost_basis=format_money(basis), gain_loss=format_money(proceeds - basis), event_ids=[event.id]))
            # Everything else (fee-only entries, UNKNOWN, TRANSFER without a
            # confirmed internal-transfer link would already be blocked above)
            # is left out of the cost-basis walk entirely.

        if underflow_assets:
            warnings.append(
                "A disposal exceeded the recorded acquired quantity for: "
                + ", ".join(sorted(underflow_assets))
                + " — cost basis for those disposals is likely understated. This usually means an "
                "acquisition is missing from the ledger (e.g. a source that hasn't been connected)."
            )

        total_fees = Decimal(0)
        fee_unpriced = False
        for event in events:
            if event.occurred_at.year != tax_year:
                continue
            for fee in event.fees:
                amount = Decimal(fee.fee_amount)
                if fee.fee_asset_id == event.primary_asset_id and Decimal(event.primary_amount):
                    total_fees += amount * (_sek_value(event) / Decimal(event.primary_amount))
                elif fee.fee_asset.coingecko_id:
                    prices = get_historical_prices(session, configured_price_provider(session), fee.fee_asset.coingecko_id, event.occurred_at, ["SEK"])
                    if "SEK" in prices:
                        total_fees += amount * prices["SEK"][0]
                    else:
                        fee_unpriced = True
                else:
                    fee_unpriced = True
        if fee_unpriced:
            warnings.append(
                "One or more fees were in an asset with no known SEK price for that day — those fees contribute "
                "0 to the total above rather than being guessed."
            )

        year_events = [e for e in events if e.occurred_at.year == tax_year]
        uncovered = uncovered_type_warning(year_events, _IN_TYPES | _OUT_TYPES | {"SWAP"}, skip_ids)
        if uncovered:
            warnings.append(uncovered)

        warnings.append(
            "Sweden limits the deductible portion of a net capital loss (across all capital income, not just "
            "crypto) to 70% — the total below is the raw crypto gain/loss; apply that limitation, if it applies, "
            "when combining with the rest of your capital income return. Verify against current Skatteverket "
            f"guidance for tax year {tax_year}."
        )

        total_gain = format_money(sum((Decimal(row.gain_loss) for row in gain_rows), Decimal(0)))
        total_income = format_money(sum((Decimal(row.fiat_value) for row in income_rows), Decimal(0)))

        asset_summary = [
            AssetSummaryRow(
                asset=asset,
                acquired_quantity=format_quantity(acquired.get(asset, Decimal(0))),
                disposed_quantity=format_quantity(disposed.get(asset, Decimal(0))),
                held_quantity=format_quantity(pos.quantity),
                cost_basis_remaining=format_money(pos.cost),
            )
            for asset, pos in sorted(positions.items())
        ]

        transfer_rows, correction_rows, event_schedule_rows, event_schedule_total, reconciliation = build_supplementary_rows(
            session, events, pairs, tax_year
        )

        return TaxCalculationResult(
            country=self.country_code,
            tax_year=tax_year,
            method=self.default_method,
            currency=self.default_currency,
            generated_at=datetime.now(timezone.utc),
            gain_loss_rows=gain_rows,
            income_rows=income_rows,
            asset_summary=asset_summary,
            total_short_term_gain=total_gain,
            total_long_term_gain="0",  # Sweden makes no holding-period distinction for this asset class
            total_income=total_income,
            total_fees=format_money(total_fees),
            warnings=warnings,
            engine=self.engine,
            acquisition_rows=acquisition_rows,
            transfer_rows=transfer_rows,
            correction_rows=correction_rows,
            event_schedule_rows=event_schedule_rows,
            event_schedule_total=event_schedule_total,
            reconciliation=reconciliation,
        )
