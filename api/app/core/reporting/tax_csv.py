from __future__ import annotations

import csv
import io

from app.core.tax.adapter import TaxCalculationResult

from .i18n import DEFAULT_LANGUAGE, t


def render_tax_csv(result: TaxCalculationResult, language: str = DEFAULT_LANGUAGE) -> str:
    """Country/year-specific tax CSV (plan §65) — deliberately separate from
    the jurisdiction-neutral Full Ledger CSV. Two sections: disposals
    (gain/loss) and income, since they're taxed differently almost
    everywhere and shouldn't be flattened into one ambiguous table."""

    def tr(key: str, **kwargs) -> str:
        return t(language, key, **kwargs)

    out = io.StringIO()
    out.write(tr("working_document_header", country=result.country, tax_year=result.tax_year, method=result.method, currency=result.currency) + "\n")
    out.write(tr("working_document_note") + "\n\n")

    out.write(f"## {tr('acquisitions')}\n")
    writer = csv.writer(out)
    writer.writerow([tr("asset"), tr("category"), tr("quantity"), f"{tr('cost_basis_established')}_{result.currency}"])
    for row in result.acquisition_rows:
        writer.writerow([row.asset, row.category, row.quantity, row.cost_basis])

    out.write(f"\n## {tr('disposals')}\n")
    writer.writerow([tr("asset"), tr("category"), tr("term"), tr("quantity"), f"{tr('proceeds')}_{result.currency}", f"{tr('cost_basis')}_{result.currency}", f"{tr('gain_loss')}_{result.currency}"])
    for row in result.gain_loss_rows:
        writer.writerow([row.asset, row.category, row.term or "", row.quantity, row.proceeds, row.cost_basis, row.gain_loss])

    out.write(f"\n## {tr('income_section')}\n")
    writer.writerow([tr("asset"), tr("category"), tr("quantity"), f"{tr('value')}_{result.currency}"])
    for row in result.income_rows:
        writer.writerow([row.asset, row.category, row.quantity, row.fiat_value])

    out.write(f"\n## {tr('transfers_section')}\n")
    writer.writerow([tr("date"), tr("asset"), tr("quantity"), tr("from_account"), tr("to_account")])
    for row in result.transfer_rows:
        writer.writerow([row.occurred_at.isoformat(), row.asset, row.quantity, row.from_label, row.to_label])

    out.write(f"\n## {tr('corrections_section')}\n")
    writer.writerow([tr("event"), tr("field"), tr("old_value"), tr("new_value"), tr("changed_at")])
    for row in result.correction_rows:
        writer.writerow([row.event_id, row.field, row.old_value, row.new_value, row.changed_at.isoformat()])

    out.write(f"\n## {tr('schedule_section')}\n")
    writer.writerow([tr("date"), tr("event_type"), tr("asset"), tr("quantity"), tr("from_account"), tr("to_account")])
    for row in result.event_schedule_rows:
        writer.writerow([row.occurred_at.isoformat(), row.event_type, row.asset, row.amount, row.source_wallet or "", row.destination_wallet or ""])

    out.write(f"\n## {tr('totals')}\n")
    writer.writerow([tr("short_term"), result.total_short_term_gain])
    writer.writerow([tr("long_term"), result.total_long_term_gain])
    writer.writerow([tr("total_income"), result.total_income])
    writer.writerow([tr("total_fees"), result.total_fees])

    return out.getvalue()
