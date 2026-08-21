from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session, selectinload

from app.core.settings import get_or_create_settings, is_under_activity_threshold
from app.db.models import Account, Event, EventLink, Override

from .i18n import DEFAULT_LANGUAGE, t
from .pdf import _kv, _ReportPDF, _safe, _section, _table, _text, _truncate

_SCHEDULE_LIMIT = 500
_CORRECTIONS_LIMIT = 500


def render_accountant_pdf(session: Session, language: str = DEFAULT_LANGUAGE) -> bytes:
    """Plan §63's universal, jurisdiction-neutral "Accountant PDF" — unlike
    the country-specific Tax PDF (core/reporting/pdf.py), this applies no
    jurisdiction's tax rules and computes no gain/loss; it's a plain-English
    summary of the canonical ledger itself, useful even before a country has
    been chosen or when a report needs to cover more than one tax year."""

    def tr(key: str, **kwargs) -> str:
        return t(language, key, **kwargs)

    events = (
        session.query(Event)
        .options(selectinload(Event.primary_asset), selectinload(Event.secondary_asset), selectinload(Event.valuations))
        .order_by(Event.occurred_at.desc())
        .all()
    )
    settings = get_or_create_settings(session)
    events = [event for event in events if not is_under_activity_threshold(event, settings)]
    visible_event_ids = {event.id for event in events}
    accounts = session.query(Account).order_by(Account.name).all()
    overrides = (
        session.query(Override)
        .filter(Override.event_id.in_(visible_event_ids) if visible_event_ids else Override.event_id == -1)
        .order_by(Override.changed_at.desc())
        .limit(_CORRECTIONS_LIMIT)
        .all()
    )
    links = (
        session.query(EventLink)
        .filter(EventLink.relationship_type == "INTERNAL_TRANSFER")
        .options(selectinload(EventLink.event), selectinload(EventLink.linked_event))
        .order_by(EventLink.created_at.desc())
        .all()
    )
    links = [link for link in links if link.event_id in visible_event_ids and link.linked_event_id in visible_event_ids]
    linked_event_ids = {event_id for link in links for event_id in (link.event_id, link.linked_event_id)}
    explicit_transfers = [
        event
        for event in events
        if event.id not in linked_event_ids and (event.event_type == "TRANSFER" or event.internal_transfer)
    ]

    pdf = _ReportPDF()
    pdf.disclaimer_text = tr("accountant_disclaimer")
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, _safe(tr("accountant_title")), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(200, 90, 0)
    _text(pdf, tr("accountant_disclaimer"))
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    _section(pdf, tr("ledger_overview"))
    _kv(pdf, tr("generated"), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    _kv(pdf, tr("total_events"), str(len(events)))
    if events:
        _kv(pdf, tr("date_range"), f"{events[-1].occurred_at.date()} - {events[0].occurred_at.date()}")

    _section(pdf, tr("accounts_section"))
    if accounts:
        _table(
            pdf,
            [tr("name"), tr("kind"), tr("connector")],
            [[a.name, a.kind, a.connector_type] for a in accounts],
            [70, 50, 70],
        )
    else:
        _text(pdf, tr("no_events"))

    _section(pdf, tr("transfers_section"))
    if links or explicit_transfers:
        _table(
            pdf,
            [tr("date"), tr("asset"), tr("quantity"), tr("from_account"), tr("to_account")],
            [
                [
                    link.event.occurred_at.date().isoformat(),
                    link.event.primary_asset.symbol,
                    link.event.primary_amount,
                    link.event.wallet_display,
                    link.linked_event.wallet_display,
                ]
                for link in links
            ]
            + [
                [
                    event.occurred_at.date().isoformat(),
                    event.primary_asset.symbol,
                    event.primary_amount,
                    event.wallet_display if event.direction == "-" else (event.address_from or "Unlinked source"),
                    (event.address_to or "Unlinked destination") if event.direction == "-" else event.wallet_display,
                ]
                for event in explicit_transfers
            ],
            [25, 20, 30, 55, 55],
        )
    else:
        _text(pdf, tr("no_transfers"))

    _section(pdf, tr("corrections_section"))
    if overrides:
        _table(
            pdf,
            [tr("event"), tr("field"), tr("old_value"), tr("new_value"), tr("changed_at")],
            [[f"#{o.event_id}", o.field, _truncate(o.old_value or "", 26), _truncate(o.new_value or "", 26), o.changed_at.date().isoformat()] for o in overrides],
            [20, 30, 45, 45, 25],
        )
    else:
        _text(pdf, tr("no_corrections"))

    _section(pdf, tr("pricing_methodology"))
    _text(pdf, tr("accountant_pricing_text"))

    _section(pdf, tr("source_info"))
    _text(pdf, tr("source_text"))

    _section(pdf, tr("schedule_section"))
    truncated = events[:_SCHEDULE_LIMIT]
    if truncated:
        _table(
            pdf,
            [tr("date"), tr("event_type"), tr("asset"), tr("quantity"), tr("from_account"), tr("to_account")],
            [
                [e.occurred_at.date().isoformat(), e.event_type, e.primary_asset.symbol, e.primary_amount, e.wallet_display, e.address_to or "-"]
                for e in truncated
            ],
            [25, 25, 20, 25, 55, 55],
        )
        if len(events) > len(truncated):
            pdf.ln(1)
            _text(pdf, tr("schedule_truncated", shown=len(truncated), total=len(events)))
    else:
        _text(pdf, tr("no_events"))

    return bytes(pdf.output())
