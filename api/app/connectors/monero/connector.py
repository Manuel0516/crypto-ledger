from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import httpx

from app.connectors.base import ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord


class MoneroConnector:
    """Talks to a monero-wallet-rpc daemon you run yourself (view-only wallet
    recommended). We never see a seed or spend key — only what the daemon's
    RPC exposes (plan §36/§81). If the daemon isn't reachable, syncing fails
    loudly rather than fabricating a transfer history."""

    source_id = "monero"

    def __init__(self, host: str, port: int, account_label: str, username: str | None = None, password: str | None = None):
        self.base = f"http://{host}:{port}/json_rpc"
        self.account_label = account_label
        self.auth = httpx.DigestAuth(username, password) if username else None

    @property
    def version(self) -> str:
        return "monero-wallet-rpc-0.1"

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        try:
            response = httpx.post(
                self.base,
                json={"jsonrpc": "2.0", "id": "0", "method": method, "params": params or {}},
                auth=self.auth,
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Could not reach monero-wallet-rpc at {self.base}: {exc}") from exc
        if "error" in data:
            raise ConnectorUnavailable(f"monero-wallet-rpc error: {data['error']}")
        return data.get("result", {})

    def test_connection(self) -> bool:
        self._rpc("get_version")
        return True

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        result = self._rpc("get_transfers", {"in": True, "out": True, "pending": False, "pool": False})
        for kind in ("in", "out"):
            for transfer in result.get(kind, []):
                timestamp = transfer.get("timestamp")
                source_timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None
                yield RawRecord(
                    f"{self.source_id}:{self.account_label}",
                    f"{transfer['txid']}-{kind}",
                    source_timestamp,
                    {**transfer, "_kind": kind},
                )

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        transfer = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        amount = transfer["amount"] / 1e12

        fees: list[NormalizedFee] = []
        # Monero is a privacy coin by design — the sender of an incoming
        # transfer is cryptographically hidden from the recipient, so
        # address_from stays None for "in" rather than guessing (plan §17's
        # "where applicable" applies literally here).
        if transfer["_kind"] == "out":
            event_type, direction = "WITHDRAWAL", "-"
            address_from = transfer.get("address")
            destinations = transfer.get("destinations") or []
            address_to = destinations[0].get("address") if destinations else None
            if transfer.get("fee"):
                fees.append(NormalizedFee(fee_type="NETWORK_FEE", asset_symbol="XMR", amount=f"{transfer['fee'] / 1e12:.12f}"))
        else:
            event_type, direction = "DEPOSIT", "+"
            address_from = None
            address_to = transfer.get("address")

        return NormalizedEvent(
            event_type=event_type,
            event_subtype="onchain",
            direction=direction,
            status="COMPLETE",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol="XMR",
            asset_network="Monero",
            amount=f"{amount:.12f}",
            account_name=self.account_label,
            address_from=address_from,
            address_to=address_to,
            notes=f"Monero tx {transfer['txid'][:12]}…",
            fees=fees,
            tx_hash=transfer.get("txid"),
            block_height=transfer.get("height") or None,
        )
