from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from app.connectors.base import NormalizedEvent, NormalizedFee, RawRecord

# Expected shape per record (matches what a Bitget export/API response is
# normalized into upstream of this connector):
#   id: str                 external id, stable across re-imports
#   type: str                buy | sell | deposit | withdrawal | transfer | payment | trade_fee
#   timestamp: str            ISO-8601
#   coin: str                 asset symbol
#   amount: str                decimal string, always positive
#   direction: "+" | "-"       optional, inferred from type when absent
#   fee: str                   optional decimal string
#   fee_coin: str               optional, defaults to `coin`
#   source / to / note: str, optional
#   tx_hash / order_id / trade_id / deposit_id / withdrawal_id: optional
#   address_from / address_to (or common camelCase/API aliases): optional

_TYPE_MAP = {
    "buy": ("BUY", "+"),
    "sell": ("SELL", "-"),
    "deposit": ("DEPOSIT", "+"),
    "withdrawal": ("WITHDRAWAL", "-"),
    "transfer": ("TRANSFER", "-"),
    "payment": ("PAYMENT", "-"),
    "staking_reward": ("STAKING_REWARD", "+"),
    "interest": ("INTEREST", "+"),
}


class BitgetConnector:
    """Read-only import boundary. Live API authentication is out of scope for
    this pass — only read-only credentials would ever be requested, and only
    when a real live-sync integration is built. For now, evidence arrives as
    a JSON export."""

    source_id = "bitget"
    version = "bitget-json-0.3"

    def fetch_json_file(self, path: str | Path) -> Iterable[RawRecord]:
        records = json.loads(Path(path).read_text())
        yield from self.fetch_records(records)

    def fetch_records(self, records: list[dict]) -> Iterable[RawRecord]:
        for item in records:
            external_id = item.get("id") or item.get("transaction_id")
            if not external_id:
                raise ValueError("Each imported record needs an id or transaction_id")
            timestamp = item.get("timestamp")
            if not timestamp:
                raise ValueError(f"Record {external_id} is missing timestamp")
            yield RawRecord(
                self.source_id,
                str(external_id),
                datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
                item,
            )

    def fetch(self, since=None) -> Iterable[RawRecord]:  # pragma: no cover - no live API in this pass
        return []

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        raw_type = str(payload.get("type", "unknown")).lower()
        event_type, default_direction = _TYPE_MAP.get(raw_type, ("UNKNOWN", "-"))
        direction = payload.get("direction") or default_direction
        occurred_at = raw.source_timestamp or datetime.fromisoformat(payload["timestamp"])

        fees: list[NormalizedFee] = []
        fee_amount = payload.get("fee")
        try:
            has_fee = fee_amount is not None and Decimal(str(fee_amount)) > 0
        except (InvalidOperation, ValueError):
            raise ValueError(f"Invalid fee amount for record {raw.external_id}")
        if has_fee:
            fees.append(
                NormalizedFee(
                    fee_type="EXCHANGE_FEE",
                    asset_symbol=str(payload.get("fee_coin") or payload.get("coin")).upper(),
                    amount=str(fee_amount),
                )
            )

        tx_hash = _first_present(payload, "tx_hash", "txHash", "transaction_hash", "transactionHash", "txId", "trHash", "hash")
        order_id = _first_present(payload, "order_id", "orderId", "orderID")
        trade_id = _first_present(payload, "trade_id", "tradeId")
        deposit_id = _first_present(payload, "deposit_id", "depositId")
        withdrawal_id = _first_present(payload, "withdrawal_id", "withdrawalId")
        address_from = _first_present(payload, "address_from", "addressFrom", "from_address", "fromAddress")
        address_to = _first_present(payload, "address_to", "addressTo", "to_address", "toAddress")
        if address_to is None and raw_type in {"deposit", "withdrawal", "transfer", "payment"}:
            address_to = _first_present(payload, "address")

        return NormalizedEvent(
            event_type=event_type,
            event_subtype=raw_type,
            direction=direction,
            status="REQUIRES_REVIEW" if event_type == "UNKNOWN" else "COMPLETE",
            occurred_at=occurred_at,
            original_timestamp=payload.get("timestamp"),
            asset_symbol=str(payload.get("coin", "")).upper(),
            amount=str(payload.get("amount", "0")),
            account_name=payload.get("source", "Bitget"),
            address_from=address_from,
            address_to=address_to or payload.get("destination"),
            notes=payload.get("note"),
            fees=fees,
            tx_hash=tx_hash,
            order_id=order_id,
            trade_id=trade_id,
            deposit_id=deposit_id,
            withdrawal_id=withdrawal_id,
        )


def _first_present(payload: dict, *keys: str) -> str | None:
    """Read an evidence field across common export/API naming conventions."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None
