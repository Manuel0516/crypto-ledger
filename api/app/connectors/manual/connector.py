from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.connectors.base import NormalizedEvent, NormalizedFee, RawRecord


class ManualConnector:
    """User-entered activity. Routed through the same raw-evidence + ledger
    pipeline as any other source so it has the same fields, valuation, fee,
    and reconciliation behavior as imported activity. ``provenance`` remains
    the audit marker; manual entry is not a separate review state."""

    source_id = "manual"
    version = "manual-0.1"

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:  # pragma: no cover
        return []

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        amount = str(payload["amount"])
        direction = "-" if amount.startswith("-") else "+"

        fees: list[NormalizedFee] = []
        for fee in payload.get("fees") or []:
            if not isinstance(fee, dict) or not fee.get("asset_symbol") or not fee.get("amount"):
                continue
            fees.append(
                NormalizedFee(
                    fee_type=str(fee.get("fee_type") or "NETWORK_FEE"),
                    asset_symbol=str(fee["asset_symbol"]).upper(),
                    amount=str(fee["amount"]),
                    fee_recipient=fee.get("fee_recipient") or None,
                )
            )

        return NormalizedEvent(
            event_type=payload.get("event_type", "MANUAL_ADJUSTMENT"),
            event_subtype=payload.get("event_subtype"),
            direction=direction,
            status="COMPLETE",
            occurred_at=raw.source_timestamp or datetime.fromisoformat(payload["occurred_at"]),
            original_timestamp=payload.get("occurred_at"),
            asset_symbol=str(payload["symbol"]).upper(),
            asset_network=payload.get("asset_network") or None,
            amount=amount.lstrip("-"),
            secondary_asset_symbol=(str(payload["secondary_symbol"]).upper() if payload.get("secondary_symbol") else None),
            secondary_asset_network=payload.get("secondary_asset_network") or None,
            secondary_amount=(str(payload["secondary_amount"]).lstrip("-") if payload.get("secondary_amount") else None),
            account_name=payload.get("address_from") or "Manual",
            source_timezone=payload.get("source_timezone"),
            address_from=payload.get("address_from") or None,
            address_to=payload.get("address_to") or None,
            fees=fees,
            tx_hash=payload.get("tx_hash") or None,
            order_id=payload.get("order_id") or None,
            trade_id=payload.get("trade_id") or None,
            deposit_id=payload.get("deposit_id") or None,
            withdrawal_id=payload.get("withdrawal_id") or None,
        )
