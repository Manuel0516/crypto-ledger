from __future__ import annotations

import csv
import io
import json

from sqlalchemy.orm import Session

from app.core.ledger.overrides import effective_values
from app.db.models import Event

FIELDNAMES = [
    "id",
    "external_id",
    "occurred_at",
    "event_type",
    "event_subtype",
    "direction",
    "status",
    "asset",
    "amount",
    "secondary_asset",
    "secondary_amount",
    "source_label",
    "destination_label",
    "counterparty",
    "address_from",
    "address_to",
    "fee_asset",
    "fee_amount",
    "eur_value",
    "sek_value",
    "valuation_provider",
    "valuation_method",
    "valuation_granularity",
    "tx_hash",
    "block_height",
    "order_id",
    "trade_id",
    "deposit_id",
    "withdrawal_id",
    "contract_address",
    "raw_payload_hash",
    "normalizer_version",
    "source_id",
    "source_timezone",
    "imported_at",
    "description",
    "merchant",
    "tags",
    "evidence_reference",
    "linked_event_ids",
    "valuation_requested_timestamp",
    "valuation_observation_timestamp",
    "valuation_fetched_at",
    "override_history",
    "notes",
]


def export_ledger_csv(session: Session) -> str:
    """Jurisdiction-neutral full ledger export (plan §64)."""
    events = session.query(Event).order_by(Event.occurred_at).all()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDNAMES)
    writer.writeheader()
    for event in events:
        valuations = {v.quote_currency: v.total_value for v in event.valuations}
        valuation_provenance = next(iter(event.valuations), None)
        values, _ = effective_values(session, event)
        linked_event_ids = sorted({
            *(link.linked_event_id for link in event.outgoing_links),
            *(link.event_id for link in event.incoming_links),
        })
        fee_assets = "; ".join(f.fee_asset.symbol for f in event.fees)
        fee_amounts = "; ".join(f.fee_amount for f in event.fees)
        writer.writerow(
            {
                "id": event.id,
                "external_id": event.external_id,
                "occurred_at": values["occurred_at"],
                "event_type": values["event_type"],
                "event_subtype": values["event_subtype"] or "",
                "direction": event.direction or "",
                "status": event.status,
                "asset": event.primary_asset.symbol,
                "amount": values["primary_amount"],
                "secondary_asset": event.secondary_asset.symbol if event.secondary_asset else "",
                "secondary_amount": values["secondary_amount"] or "",
                "source_label": values["source_label"],
                "destination_label": values["destination_label"] or "",
                "counterparty": values["counterparty"] or "",
                "address_from": values["address_from"] or "",
                "address_to": values["address_to"] or "",
                "fee_asset": fee_assets,
                "fee_amount": fee_amounts,
                "eur_value": valuations.get("EUR", ""),
                "sek_value": valuations.get("SEK", ""),
                "valuation_provider": valuation_provenance.provider if valuation_provenance else "",
                "valuation_method": valuation_provenance.method if valuation_provenance else "",
                "valuation_granularity": valuation_provenance.granularity if valuation_provenance else "",
                "tx_hash": values["tx_hash"] or "",
                "block_height": event.block_height if event.block_height is not None else "",
                "order_id": values["order_id"] or "",
                "trade_id": values["trade_id"] or "",
                "deposit_id": values["deposit_id"] or "",
                "withdrawal_id": values["withdrawal_id"] or "",
                "contract_address": values["contract_address"] or "",
                "raw_payload_hash": event.raw_event.payload_hash if event.raw_event else "",
                "normalizer_version": event.normalizer_version,
                "source_id": event.raw_event.source_id if event.raw_event else "",
                "source_timezone": event.source_timezone or "",
                "imported_at": event.raw_event.received_at.isoformat() if event.raw_event else event.created_at.isoformat(),
                "description": values.get("description") or "",
                "merchant": values.get("merchant") or "",
                "tags": "; ".join(json.loads(values.get("tags_json") or "[]")),
                "evidence_reference": values.get("evidence_reference") or "",
                "linked_event_ids": "; ".join(str(event_id) for event_id in linked_event_ids),
                "valuation_requested_timestamp": valuation_provenance.requested_timestamp.isoformat() if valuation_provenance else "",
                "valuation_observation_timestamp": valuation_provenance.observation_timestamp.isoformat() if valuation_provenance else "",
                "valuation_fetched_at": valuation_provenance.fetched_at.isoformat() if valuation_provenance else "",
                "override_history": json.dumps([
                    {"field": override.field, "old": override.old_value, "new": override.new_value, "at": override.changed_at.isoformat(), "reason": override.reason}
                    for override in sorted(event.overrides, key=lambda override: override.id or 0)
                ], ensure_ascii=False),
                "notes": values["notes"] or "",
            }
        )
    return out.getvalue()
