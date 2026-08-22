from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import httpx

from app.connectors.base import ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

# Talks to your own LND node's REST API (plan §37). Field names follow LND's
# documented REST API (lnrpc); this has been exercised against real Bitcoin/
# EVM/Solana/exchange APIs in this codebase, but — unlike those — there is
# no LND node reachable in the environment this was built in to test
# against live. Every field is read defensively (multiple fallback key
# names, skip-not-crash on a missing one) so a version difference degrades
# rather than breaks; treat the first sync against a real node as the real
# verification and report back anything that looks off.
#
# Channel open/close capacity is recorded as its own event without linking
# to the underlying Bitcoin funding/closing transaction — plan §37 wants
# that reconciliation eventually, but it isn't implemented here, so if the
# same wallet also has its on-chain Bitcoin funding source connected, the
# channel capacity may appear to double-count against it.


class LightningConnector:
    source_id = "lightning"

    def __init__(self, host: str, macaroon_hex: str, account_label: str):
        self.host = host.rstrip("/")
        self.macaroon_hex = macaroon_hex
        self.account_label = account_label

    @property
    def version(self) -> str:
        return "lightning-lnd-0.3"

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = httpx.get(
                f"{self.host}{path}",
                headers={"Grpc-Metadata-macaroon": self.macaroon_hex},
                params=params,
                timeout=15.0,
                verify=False,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Could not reach the LND node at {self.host}: {exc}") from exc
        except ValueError as exc:
            raise ConnectorUnavailable(f"LND returned a non-JSON response from {path}") from exc

    def _get_optional(self, path: str, params: dict | None = None) -> dict:
        try:
            return self._get(path, params)
        except ConnectorUnavailable:
            return {}

    def test_connection(self) -> bool:
        self._get("/v1/getinfo")
        return True

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        for payment in self._get_optional("/v1/payments", {"include_incomplete": "false", "max_payments": "500"}).get("payments") or []:
            if payment.get("status") not in (None, "SUCCEEDED"):
                continue
            payment_hash = payment.get("payment_hash")
            if not payment_hash:
                continue
            payload = {**payment, "_kind": "payment"}
            yield RawRecord(self.source_id, f"payment-{payment_hash}", _payment_timestamp(payment), payload)

        for invoice in self._get_optional("/v1/invoices", {"pending_only": "false", "num_max_invoices": "1000"}).get("invoices") or []:
            if not invoice.get("settled"):
                continue
            r_hash = invoice.get("r_hash")
            if not r_hash:
                continue
            payload = {**invoice, "_kind": "invoice"}
            yield RawRecord(self.source_id, f"invoice-{r_hash}", _invoice_timestamp(invoice), payload)

        for channel in self._get_optional("/v1/channels").get("channels") or []:
            chan_id = channel.get("chan_id")
            if not chan_id:
                continue
            payload = {**channel, "_kind": "channel_open"}
            yield RawRecord(self.source_id, f"channel-open-{chan_id}", None, payload)

        for channel in self._get_optional("/v1/channels/closed").get("channels") or []:
            chan_id = channel.get("chan_id")
            if not chan_id:
                continue
            payload = {**channel, "_kind": "channel_close"}
            yield RawRecord(self.source_id, f"channel-close-{chan_id}", None, payload)

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        kind = payload["_kind"]
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)

        if kind == "payment":
            value_msat = _first_int(payload, "value_msat") or _first_int(payload, "value_sat", "value") * 1000
            fee_msat = _first_int(payload, "fee_msat") or _first_int(payload, "fee_sat", "fee") * 1000
            fees = []
            if fee_msat:
                fees.append(NormalizedFee(fee_type="LIGHTNING_FEE", asset_symbol="BTC", amount=_msat_to_btc(fee_msat)))
            return NormalizedEvent(
                event_type="WITHDRAWAL",
                event_subtype="lightning",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                # No asset_network: BTC moved over Lightning is still BTC —
                # the same fungible holding as on-chain BTC, not a distinct
                # asset the way a bridged token is. Leaving this unset
                # resolves to the ledger's canonical "Bitcoin" network via
                # get_or_create_asset, so on-chain + Lightning balances sum
                # into one portfolio figure (event_subtype still
                # distinguishes them in Activity). Previously tagged
                # "Lightning" here, which fragmented a user's real BTC
                # holdings into two separate line items — and, since the
                # Overview page keys its cards by symbol, produced a
                # duplicate-key render bug whenever both were connected.
                asset_symbol="BTC",
                amount=_msat_to_btc(value_msat),
                account_name=self.account_label,
                address_to=payload.get("destination") or payload.get("payee") or payload.get("destination_pubkey"),
                notes=f"Lightning payment · {payload.get('payment_hash', '')[:16]}",
                fees=fees,
                tx_hash=payload.get("payment_hash"),
            )

        if kind == "invoice":
            value_msat = _first_int(payload, "value_msat") or _first_int(payload, "value") * 1000
            return NormalizedEvent(
                event_type="DEPOSIT",
                event_subtype="lightning",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                # No asset_network: BTC moved over Lightning is still BTC —
                # the same fungible holding as on-chain BTC, not a distinct
                # asset the way a bridged token is. Leaving this unset
                # resolves to the ledger's canonical "Bitcoin" network via
                # get_or_create_asset, so on-chain + Lightning balances sum
                # into one portfolio figure (event_subtype still
                # distinguishes them in Activity). Previously tagged
                # "Lightning" here, which fragmented a user's real BTC
                # holdings into two separate line items — and, since the
                # Overview page keys its cards by symbol, produced a
                # duplicate-key render bug whenever both were connected.
                asset_symbol="BTC",
                amount=_msat_to_btc(value_msat),
                account_name=self.account_label,
                notes=f"Lightning invoice settled{' · ' + payload['memo'] if payload.get('memo') else ''}",
                tx_hash=payload.get("r_hash"),
            )

        if kind == "channel_open":
            capacity_sat = _first_int(payload, "capacity")
            return NormalizedEvent(
                event_type="STAKING_DEPOSIT",
                event_subtype="channel",
                direction="-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                # No asset_network: BTC moved over Lightning is still BTC —
                # the same fungible holding as on-chain BTC, not a distinct
                # asset the way a bridged token is. Leaving this unset
                # resolves to the ledger's canonical "Bitcoin" network via
                # get_or_create_asset, so on-chain + Lightning balances sum
                # into one portfolio figure (event_subtype still
                # distinguishes them in Activity). Previously tagged
                # "Lightning" here, which fragmented a user's real BTC
                # holdings into two separate line items — and, since the
                # Overview page keys its cards by symbol, produced a
                # duplicate-key render bug whenever both were connected.
                asset_symbol="BTC",
                amount=f"{capacity_sat / 1e8:.8f}",
                account_name=self.account_label,
                address_to=payload.get("remote_pubkey"),
                notes=(
                    f"Channel opened · funding {payload.get('channel_point', '')} — "
                    "not reconciled against the on-chain funding tx if you also track this wallet's Bitcoin address"
                ),
                tx_hash=_funding_txid(payload.get("channel_point")),
            )

        # channel_close
        settled_sat = _first_int(payload, "settled_balance")
        return NormalizedEvent(
            event_type="STAKING_WITHDRAWAL",
            event_subtype="channel",
            direction="+",
            status="REQUIRES_REVIEW",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol="BTC",  # see the on-chain-unification note above
            amount=f"{settled_sat / 1e8:.8f}",
            account_name=self.account_label,
            address_to=payload.get("remote_pubkey"),
            notes=f"Channel closed · {payload.get('closing_tx_hash', '')}",
            tx_hash=payload.get("closing_tx_hash"),
        )


def _funding_txid(channel_point: str | None) -> str | None:
    """channel_point is "<funding_txid>:<output_index>" — the on-chain
    evidence for a channel open, when LND provides it."""
    if not channel_point or ":" not in channel_point:
        return None
    return channel_point.split(":", 1)[0] or None


def _first_int(payload: dict, *keys: str) -> int:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _msat_to_btc(value_msat: int) -> str:
    return f"{value_msat / 1e11:.11f}"  # 1 BTC = 1e11 msat


def _payment_timestamp(payment: dict) -> datetime | None:
    ns = payment.get("creation_time_ns")
    if ns:
        try:
            return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    seconds = payment.get("creation_date")
    if seconds:
        try:
            return datetime.fromtimestamp(int(seconds), tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def _invoice_timestamp(invoice: dict) -> datetime | None:
    seconds = invoice.get("settle_date") or invoice.get("creation_date")
    if seconds:
        try:
            return datetime.fromtimestamp(int(seconds), tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None
