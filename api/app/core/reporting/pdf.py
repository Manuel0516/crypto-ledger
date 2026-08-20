from __future__ import annotations

from decimal import Decimal

from fpdf import FPDF

from app.core.tax.adapter import TaxCalculationResult

from .i18n import DEFAULT_LANGUAGE, t

# fpdf2's built-in core fonts (Helvetica etc.) are Latin-1 only — Spanish
# accents (á, é, í, ó, ú, ñ, ¿, ¡) are all valid Latin-1 and render fine;
# only genuinely non-Latin-1 characters (em-dashes, curly quotes, ellipses —
# routinely present in dynamic adapter warnings) need normalizing.
_CHAR_MAP = {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."}


def _safe(text: str) -> str:
    for src, dst in _CHAR_MAP.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


class _ReportPDF(FPDF):
    disclaimer_text = ""

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120, 120, 120)
        self.multi_cell(0, 3.5, _safe(self.disclaimer_text), align="C")
        self.set_text_color(0, 0, 0)


def _section(pdf: FPDF, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(0, 8, _safe(title), new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.ln(1)


def _kv(pdf: FPDF, label: str, value: str) -> None:
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(65, 5.5, _safe(label))
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5.5, _safe(value), new_x="LMARGIN", new_y="NEXT")


def _table(pdf: FPDF, headers: list[str], rows: list[list[str]], widths: list[int]) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(250, 250, 250)
    for header, width in zip(headers, widths):
        pdf.cell(width, 6, _safe(header), border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for row in rows:
        for value, width in zip(row, widths):
            pdf.cell(width, 5.5, _safe(str(value)), border=1, align="R" if _looks_numeric(value) else "L")
        pdf.ln()


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 2] + ".."


def _looks_numeric(value: str) -> bool:
    try:
        Decimal(str(value))
        return True
    except Exception:
        return False


def _text(pdf: FPDF, text: str) -> None:
    pdf.multi_cell(0, 5, _safe(text))


def render_tax_report_pdf(result: TaxCalculationResult, taxpayer_name: str, language: str = DEFAULT_LANGUAGE) -> bytes:
    """Renders plan §66's full report section list as a working document —
    country-agnostic: everything it draws from is the shared
    TaxCalculationResult shape, not adapter internals (plan §56). The fixed
    report chrome is translated per `language`; the dynamically-generated
    methodology warnings stay in English regardless (see i18n.py)."""

    def tr(key: str, **kwargs) -> str:
        return t(language, key, **kwargs)

    pdf = _ReportPDF()
    pdf.disclaimer_text = tr("disclaimer")
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, _safe(tr("title")), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(200, 90, 0)
    _text(pdf, tr("disclaimer"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    _section(pdf, tr("report_identity"))
    _kv(pdf, tr("taxpayer"), taxpayer_name)
    _kv(pdf, tr("jurisdiction"), result.country)
    _kv(pdf, tr("tax_year"), str(result.tax_year))
    _kv(pdf, tr("accounting_method"), result.method)
    _kv(pdf, tr("currency"), result.currency)
    _kv(pdf, tr("generated"), result.generated_at.strftime("%Y-%m-%d %H:%M UTC"))

    _section(pdf, tr("summary"))
    _kv(pdf, f"{tr('short_term')} ({result.currency})", result.total_short_term_gain)
    _kv(pdf, f"{tr('long_term')} ({result.currency})", result.total_long_term_gain)
    net = Decimal(result.total_short_term_gain) + Decimal(result.total_long_term_gain)
    _kv(pdf, f"{tr('net_gain')} ({result.currency})", str(net))
    _kv(pdf, f"{tr('total_income')} ({result.currency})", result.total_income)
    _kv(pdf, f"{tr('total_fees')} ({result.currency})", result.total_fees)

    _section(pdf, tr("assets"))
    if result.asset_summary:
        _table(
            pdf,
            [tr("asset"), tr("acquired"), tr("disposed"), tr("held"), tr("cost_basis_remaining")],
            [[a.asset, a.acquired_quantity, a.disposed_quantity, a.held_quantity, a.cost_basis_remaining] for a in result.asset_summary],
            [25, 35, 35, 35, 55],
        )
    else:
        _text(pdf, tr("no_assets"))

    _section(pdf, tr("acquisitions"))
    if result.acquisition_rows:
        _table(
            pdf,
            [tr("asset"), tr("category"), tr("quantity"), f"{tr('cost_basis_established')} ({result.currency})"],
            [[r.asset, r.category, r.quantity, r.cost_basis] for r in result.acquisition_rows],
            [25, 35, 45, 45],
        )
    else:
        _text(pdf, tr("no_acquisitions"))

    _section(pdf, tr("disposals"))
    if result.gain_loss_rows:
        _table(
            pdf,
            [tr("asset"), tr("category"), tr("term"), tr("quantity"), tr("proceeds"), tr("cost_basis"), tr("gain_loss")],
            [[r.asset, r.category, r.term or "-", r.quantity, r.proceeds, r.cost_basis, r.gain_loss] for r in result.gain_loss_rows],
            [20, 25, 18, 25, 27, 27, 27],
        )
    else:
        _text(pdf, tr("no_disposals"))

    _section(pdf, tr("income_section"))
    if result.income_rows:
        _table(
            pdf,
            [tr("asset"), tr("category"), tr("quantity"), f"{tr('value')} ({result.currency})"],
            [[r.asset, r.category, r.quantity, r.fiat_value] for r in result.income_rows],
            [25, 35, 45, 45],
        )
    else:
        _text(pdf, tr("no_income"))

    _section(pdf, tr("fees_section"))
    _kv(pdf, f"{tr('total_fees')} ({result.currency})", result.total_fees)
    if result.engine == "rp2":
        _text(pdf, tr("fees_folded_note"))
    elif result.total_fees == "0.00":
        _text(pdf, tr("no_fees"))

    _section(pdf, tr("transfers_section"))
    if result.transfer_rows:
        _table(
            pdf,
            [tr("date"), tr("asset"), tr("quantity"), tr("from_account"), tr("to_account")],
            [[r.occurred_at.date().isoformat(), r.asset, r.quantity, r.from_label, r.to_label] for r in result.transfer_rows],
            [25, 20, 30, 55, 55],
        )
    else:
        _text(pdf, tr("no_transfers"))

    _section(pdf, tr("corrections_section"))
    if result.correction_rows:
        _table(
            pdf,
            [tr("event"), tr("field"), tr("old_value"), tr("new_value"), tr("changed_at")],
            [[f"#{r.event_id}", r.field, _truncate(r.old_value, 26), _truncate(r.new_value, 26), r.changed_at.date().isoformat()] for r in result.correction_rows],
            [20, 30, 45, 45, 25],
        )
    else:
        _text(pdf, tr("no_corrections"))

    _section(pdf, tr("pricing_methodology"))
    _text(pdf, tr("pricing_text", currency=result.currency))

    _section(pdf, tr("source_info"))
    _text(pdf, tr("source_text"))

    _section(pdf, tr("reconciliation_section"))
    if result.reconciliation:
        _kv(pdf, tr("linked_transfers"), str(result.reconciliation.linked_transfer_count))
        _kv(pdf, tr("unresolved_issues"), str(result.reconciliation.unresolved_issue_count))
        _kv(pdf, tr("manual_events"), str(result.reconciliation.manual_event_count))

    _section(pdf, tr("schedule_section"))
    if result.event_schedule_rows:
        _table(
            pdf,
            [tr("date"), tr("event_type"), tr("asset"), tr("quantity"), tr("counterparty")],
            [
                [r.occurred_at.date().isoformat(), r.event_type, r.asset, r.amount, r.counterparty or "-"]
                for r in result.event_schedule_rows
            ],
            [25, 35, 20, 30, 75],
        )
        if result.event_schedule_total and result.event_schedule_total > len(result.event_schedule_rows):
            pdf.ln(1)
            _text(pdf, tr("schedule_truncated", shown=len(result.event_schedule_rows), total=result.event_schedule_total))
    else:
        _text(pdf, tr("no_events"))

    _section(pdf, tr("methodology"))
    if result.warnings:
        for warning in result.warnings:
            pdf.set_font("Helvetica", "", 9)
            _text(pdf, f"- {warning}")
            pdf.ln(1)
    else:
        _text(pdf, tr("no_warnings"))

    return bytes(pdf.output())
