from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

BASE_URL = "https://api.bitget.com"

# Bitget's V2 "Classic Account" API. Read-only endpoints only — this
# connector never signs a trade or withdrawal request (plan §81/§30). Core
# spot endpoints (fills, deposits, withdrawals) are well-exercised;
# margin/futures/earn use the same V2 surface but are lower-traffic paths
# for this connector, so each product category is fetched independently —
# a permission error or endpoint drift on one (e.g. a key without futures
# access) degrades that category to "nothing pulled this round" rather
# than breaking the rest.
#
# Bitget has since introduced Unified Trading Accounts (UTA): an account
# migrated to UTA mode rejects every classic V2 endpoint above with API
# error 40085 ("You are in Unified Account mode, and the Classic Account
# API is not supported at this time") — it isn't a permission issue, it's a
# completely separate endpoint family (V3, same host + auth scheme). Mode
# is auto-detected per sync (a fresh connector is built each run, so it
# can't be cached across syncs) by probing the classic endpoint first and
# switching to V3 only on a confirmed 40085; any other error still raises
# normally so a real credential/network problem isn't misread as "must be
# UTA". V3 endpoints require an explicit startTime/endTime window (max 90
# days of history, ≤30 days per call), so UTA fetches are paginated in
# 30-day windows via cursor.
_UTA_WINDOW_MS = 30 * 24 * 3600 * 1000
_UTA_LOOKBACK_MS = 90 * 24 * 3600 * 1000

# financial-records is a broad, noisy ledger — it includes one entry per
# order fill (ORDER_DEALT_IN/OUT, OPEN_LONG, CLOSE_SHORT, ...) which would
# duplicate what /api/v3/trade/fills already yields as BUY/SELL. Only the
# types below (transfers, interest, funding fees, liquidation, margin
# borrow/repay) aren't already covered by fills, so only these are mapped;
# everything else is skipped rather than guessed (avoids double-counting).
_UTA_FINANCIAL_TYPE_MAP = {
    "TRANSFER_IN": ("TRANSFER", "+"),
    "TRANSFER_OUT": ("TRANSFER", "-"),
    "INTEREST_SETTLEMENT_OUT": ("FUNDING_PAYMENT", "-"),
    "CONTRACT_MAIN_SETTLE_FEE_USER_IN": ("FUNDING_PAYMENT", "+"),
    "CONTRACT_MAIN_SETTLE_FEE_USER_OUT": ("FUNDING_PAYMENT", "-"),
    "BURST_CLOSE_LONG": ("LIQUIDATION", "-"),
    "BURST_CLOSE_SHORT": ("LIQUIDATION", "-"),
    "BURST_BUY_SSM": ("LIQUIDATION", "-"),
    "BURST_SELL_SSM": ("LIQUIDATION", "-"),
    "BORROW": ("MARGIN_BORROW", "+"),
    "REPAYMENT": ("MARGIN_REPAY", "-"),
    "INTEREST_REPAYMENT": ("MARGIN_REPAY", "-"),
}


class BitgetApiError(ConnectorUnavailable):
    """A Bitget API call returned a non-success `code`. Carries the code
    separately from the message so callers (mode detection) can react to a
    specific code like 40085 instead of substring-matching the text."""

    def __init__(self, code, msg):
        super().__init__(f"Bitget API error {code}: {msg}")
        self.code = str(code)


class BitgetLiveConnector:
    source_id = "bitget"

    def __init__(self, api_key: str, api_secret: str, passphrase: str, account_label: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.account_label = account_label
        self.mode: str | None = None  # "classic" | "uta", detected lazily

    @property
    def version(self) -> str:
        return "bitget-live-0.4"

    @property
    def history_limit_note(self) -> str | None:
        """Surfaced by sync_account() after a backfill — a real, unavoidable
        API limit (not something more pagination fixes), so the user should
        know why history stops there rather than assume the sync is broken."""
        if self.mode == "uta":
            return "Bitget's Unified Account API only exposes the last 90 days of history — anything older isn't retrievable through this connection."
        return None

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _get(self, path: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{query}" if query else path
        timestamp = str(int(time.time() * 1000))
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": self._sign(timestamp, "GET", request_path),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        try:
            response = httpx.get(BASE_URL + request_path, headers=headers, timeout=15.0)
            data = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(f"Could not reach Bitget: {exc}") from exc
        except ValueError as exc:
            raise ConnectorUnavailable(f"Bitget returned a non-JSON response (HTTP {response.status_code})") from exc
        if str(data.get("code")) not in ("0", "00000"):
            raise BitgetApiError(data.get("code"), data.get("msg"))
        return data.get("data") or []

    def _get_optional(self, path: str, params: dict | None = None) -> list[dict]:
        """Like _get, but a failure (missing permission, endpoint drift on a
        less-exercised product) just means this category contributes
        nothing this round instead of aborting the whole sync."""
        try:
            return self._get(path, params)
        except ConnectorUnavailable:
            return []

    def _detect_mode(self) -> None:
        try:
            self._get("/api/v2/spot/account/info")
            self.mode = "classic"
        except BitgetApiError as exc:
            if exc.code != "40085":
                raise
            self.mode = "uta"

    def test_connection(self) -> bool:
        if self.mode is None:
            self._detect_mode()
            return True
        if self.mode == "uta":
            self._get("/api/v3/account/info")
        else:
            self._get("/api/v2/spot/account/info")
        return True

    # -- UTA (V3) pagination helpers -----------------------------------

    def _v3_page(self, path: str, params: dict) -> tuple[list[dict], str | None]:
        data = self._get(path, params)
        if isinstance(data, dict):
            return list(data.get("list") or []), data.get("cursor")
        if isinstance(data, list):
            return data, None
        return [], None

    def _v3_paged(self, path: str, params: dict) -> Iterable[dict]:
        cursor: str | None = None
        while True:
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            items, cursor = self._v3_page(path, page_params)
            yield from items
            if not cursor or not items:
                break

    def _v3_windowed(self, path: str, params: dict, start_ms: int, end_ms: int, *, optional: bool = False) -> Iterable[dict]:
        """Page through a V3 endpoint across ≤30-day windows spanning
        [start_ms, end_ms). `optional=True` degrades a failure on any window
        to "nothing further from this endpoint" instead of aborting fetch()."""
        window_start = start_ms
        while window_start < end_ms:
            window_end = min(window_start + _UTA_WINDOW_MS, end_ms)
            page_params = {**params, "startTime": str(window_start), "endTime": str(window_end), "limit": "100"}
            try:
                yield from self._v3_paged(path, page_params)
            except ConnectorUnavailable:
                if optional:
                    return
                raise
            window_start = window_end

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        if self.mode is None:
            self._detect_mode()
        if self.mode == "uta":
            yield from self._fetch_uta(since)
        else:
            yield from self._fetch_classic(since)

    def _fetch_uta(self, since: datetime | None = None) -> Iterable[RawRecord]:
        now_ms = int(time.time() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _UTA_LOOKBACK_MS
        start_ms = max(start_ms, now_ms - _UTA_LOOKBACK_MS)  # 90-day hard API limit
        end_ms = now_ms

        for category in ("SPOT", "MARGIN"):
            for fill in self._v3_windowed("/api/v3/trade/fills", {"category": category}, start_ms, end_ms, optional=(category != "SPOT")):
                payload = {**fill, "_kind": "uta_fill", "_category": category}
                yield RawRecord(self.source_id, f"uta-fill-{fill.get('execId')}", _ms(fill.get("createdTime")), payload)

        for record in self._v3_windowed("/api/v3/account/deposit-records", {}, start_ms, end_ms):
            payload = {**record, "_kind": "uta_deposit"}
            record_id = record.get("recordId") or record.get("orderId")
            yield RawRecord(self.source_id, f"uta-deposit-{record_id}", _ms(record.get("createdTime")), payload)

        for record in self._v3_windowed("/api/v3/account/withdrawal-records", {}, start_ms, end_ms):
            payload = {**record, "_kind": "uta_withdrawal"}
            record_id = record.get("recordId") or record.get("orderId")
            yield RawRecord(self.source_id, f"uta-withdrawal-{record_id}", _ms(record.get("createdTime")), payload)

        for category in ("SPOT", "MARGIN", "USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES", "OTHER"):
            for record in self._v3_windowed("/api/v3/account/financial-records", {"category": category}, start_ms, end_ms, optional=True):
                if record.get("type") not in _UTA_FINANCIAL_TYPE_MAP:
                    continue
                payload = {**record, "_kind": "uta_financial", "_category": category}
                yield RawRecord(self.source_id, f"uta-financial-{record.get('id')}", _ms(record.get("ts")), payload)

        for record in self._v3_windowed("/api/v3/account/convert-records", {}, start_ms, end_ms, optional=True):
            payload = {**record, "_kind": "uta_convert"}
            key = f"{record.get('ts')}-{record.get('fromCoin')}-{record.get('toCoin')}"
            yield RawRecord(self.source_id, f"uta-convert-{key}", _ms(record.get("ts")), payload)

    def fetch_balances(self) -> Iterable[Balance]:
        if self.mode is None:
            self._detect_mode()
        if self.mode == "uta":
            data = self._get("/api/v3/account/assets")
            assets = data.get("assets", []) if isinstance(data, dict) else []
            return [
                Balance(str(a.get("coin", "")).upper(), str(a.get("balance", "0")))
                for a in assets
                if _positive_amount(a.get("balance"))
            ]
        records = self._get("/api/v2/spot/account/assets")
        balances: list[Balance] = []
        for record in records:
            try:
                total = Decimal(str(record.get("available", "0"))) + Decimal(str(record.get("frozen", "0"))) + Decimal(str(record.get("locked", "0")))
            except (InvalidOperation, ValueError):
                continue
            if total > 0:
                balances.append(Balance(str(record.get("coin", "")).upper(), format(total, "f")))
        return balances

    def _fetch_classic(self, since: datetime | None = None) -> Iterable[RawRecord]:
        params: dict = {"limit": "100"}
        if since is not None:
            params["startTime"] = str(int(since.timestamp() * 1000))

        for fill in self._get("/api/v2/spot/trade/fills", params):
            payload = {**fill, "_kind": "fill"}
            yield RawRecord(self.source_id, f"fill-{fill['tradeId']}", _ms(fill.get("cTime")), payload)

        for record in self._get("/api/v2/spot/wallet/deposit-records", params):
            payload = {**record, "_kind": "deposit"}
            yield RawRecord(self.source_id, f"deposit-{record['orderId']}", _ms(record.get("cTime")), payload)

        for record in self._get("/api/v2/spot/wallet/withdrawal-records", params):
            payload = {**record, "_kind": "withdrawal"}
            yield RawRecord(self.source_id, f"withdrawal-{record['orderId']}", _ms(record.get("cTime")), payload)

        # Cross-margin borrow/repay history.
        for record in self._get_optional("/api/v2/margin/crossed/borrow-history", params):
            payload = {**record, "_kind": "margin_borrow"}
            yield RawRecord(self.source_id, f"margin-borrow-{record.get('loanId', record.get('cTime'))}", _ms(record.get("cTime")), payload)
        for record in self._get_optional("/api/v2/margin/crossed/repay-history", params):
            payload = {**record, "_kind": "margin_repay"}
            yield RawRecord(self.source_id, f"margin-repay-{record.get('repayId', record.get('cTime'))}", _ms(record.get("cTime")), payload)

        # Earn/savings subscribe, redeem, and interest records.
        for record in self._get_optional("/api/v2/earn/savings/records", {**params, "periodType": "flexible"}):
            kind = str(record.get("orderType", "")).lower()
            mapped = {"subscribe": "earn_subscribe", "redeem": "earn_redeem", "interest": "earn_interest"}.get(kind)
            if mapped is None:
                continue
            payload = {**record, "_kind": mapped}
            yield RawRecord(self.source_id, f"earn-{record.get('orderId', record.get('cTime'))}", _ms(record.get("cTime")), payload)

        # Futures ("mix") account bill — a unified ledger covering funding
        # fees, realized P/L, and similar entries, tagged by business type.
        for product in ("USDT-FUTURES", "COIN-FUTURES"):
            for record in self._get_optional("/api/v2/mix/account/bill", {**params, "productType": product}):
                payload = {**record, "_kind": "futures_bill", "_productType": product}
                yield RawRecord(self.source_id, f"futures-bill-{record.get('billId', record.get('cTime'))}", _ms(record.get("cTime")), payload)

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        kind = payload["_kind"]

        if kind == "fill":
            side = str(payload.get("side", "buy")).lower()
            base_asset, detected_quote_asset = _pair_assets(payload.get("symbol", ""))
            quote_asset = str(payload.get("quoteCoin") or detected_quote_asset or "").upper() or None
            return NormalizedEvent(
                event_type="BUY" if side == "buy" else "SELL",
                event_subtype="spot",
                direction="+" if side == "buy" else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=base_asset,
                amount=str(payload.get("size", "0")),
                source_label=self.account_label,
                notes=f"Bitget spot {side} · {payload.get('symbol')}",
                fees=_spot_fees(payload),
                secondary_asset_symbol=quote_asset,
                # V2 spot fills expose `amount` as the total quote-coin
                # notional. Keep aliases for exported/older payloads, but do
                # not fabricate a second leg when none is supplied.
                secondary_amount=_positive_amount(
                    payload.get("amount") or payload.get("quoteVolume") or payload.get("quoteQty")
                ),
                trade_id=payload.get("tradeId"),
                order_id=payload.get("orderId"),
            )

        if kind == "deposit":
            return NormalizedEvent(
                event_type="DEPOSIT",
                event_subtype="exchange",
                direction="+",
                status="COMPLETE" if payload.get("status") == "success" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("size", "0")),
                source_label=self.account_label,
                address_to=payload.get("toAddress"),
                notes=f"Bitget deposit · {payload.get('orderId')}",
                deposit_id=payload.get("orderId"),
                tx_hash=payload.get("txId") or payload.get("trHash"),
            )

        if kind == "withdrawal":
            fee_amount = payload.get("fee")
            fees = []
            if fee_amount:
                fees.append(NormalizedFee(fee_type="EXCHANGE_FEE", asset_symbol=str(payload.get("coin", "")).upper(), amount=str(abs(float(fee_amount)))))
            return NormalizedEvent(
                event_type="WITHDRAWAL",
                event_subtype="exchange",
                direction="-",
                status="COMPLETE" if payload.get("status") == "success" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("size", "0")),
                source_label=self.account_label,
                destination_label=payload.get("toAddress"),
                address_to=payload.get("toAddress"),
                notes=f"Bitget withdrawal · {payload.get('orderId')}",
                fees=fees,
                withdrawal_id=payload.get("orderId"),
                tx_hash=payload.get("txId") or payload.get("trHash"),
            )

        if kind == "margin_borrow":
            return NormalizedEvent(
                event_type="MARGIN_BORROW",
                event_subtype="cross_margin",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("borrowAmount", payload.get("amount", "0"))),
                source_label=self.account_label,
                notes=f"Bitget cross-margin borrow · {payload.get('loanId', '')}",
                order_id=payload.get("loanId"),
            )

        if kind == "margin_repay":
            return NormalizedEvent(
                event_type="MARGIN_REPAY",
                event_subtype="cross_margin",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("repayAmount", payload.get("amount", "0"))),
                source_label=self.account_label,
                notes=f"Bitget cross-margin repay · {payload.get('repayId', '')}",
                order_id=payload.get("repayId"),
            )

        if kind in ("earn_subscribe", "earn_redeem", "earn_interest"):
            event_type = {"earn_subscribe": "STAKING_DEPOSIT", "earn_redeem": "STAKING_WITHDRAWAL", "earn_interest": "STAKING_REWARD"}[kind]
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="savings",
                direction="-" if kind == "earn_subscribe" else "+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", payload.get("productCoin", ""))).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                notes=f"Bitget earn ({kind.split('_')[1]}) · {payload.get('orderId', '')}",
                order_id=payload.get("orderId"),
            )

        if kind == "uta_fill":
            side = str(payload.get("side", "buy")).lower()
            base_asset, detected_quote_asset = _pair_assets(payload.get("symbol", ""))
            category = str(payload.get("_category", "")).lower()
            return NormalizedEvent(
                event_type="BUY" if side == "buy" else "SELL",
                event_subtype=category or "spot",
                direction="+" if side == "buy" else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=base_asset,
                amount=str(payload.get("execQty", "0")),
                source_label=self.account_label,
                notes=f"Bitget {category or 'spot'} {side} · {payload.get('symbol')}",
                fees=_uta_fill_fees(payload),
                secondary_asset_symbol=detected_quote_asset,
                secondary_amount=_positive_amount(payload.get("execValue")),
                trade_id=payload.get("execId"),
                order_id=payload.get("orderId"),
            )

        if kind in ("uta_deposit", "uta_withdrawal"):
            is_deposit = kind == "uta_deposit"
            on_chain = payload.get("dest") == "on_chain"
            fees = []
            if not is_deposit and payload.get("fee"):
                fees.append(NormalizedFee(fee_type="EXCHANGE_FEE", asset_symbol=str(payload.get("coin", "")).upper(), amount=str(abs(float(payload["fee"])))))
            return NormalizedEvent(
                event_type="DEPOSIT" if is_deposit else "WITHDRAWAL",
                event_subtype="exchange",
                direction="+" if is_deposit else "-",
                status="COMPLETE" if payload.get("status") == "success" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("size", "0")),
                source_label=self.account_label,
                address_to=payload.get("toAddress"),
                destination_label=payload.get("toAddress") if not is_deposit else None,
                notes=f"Bitget {'deposit' if is_deposit else 'withdrawal'} · {payload.get('orderId')}",
                fees=fees,
                deposit_id=payload.get("orderId") if is_deposit else None,
                withdrawal_id=payload.get("orderId") if not is_deposit else None,
                # recordId is the on-chain tx hash only for on-chain moves;
                # for an internal transfer it's just another order id.
                tx_hash=payload.get("recordId") if on_chain else None,
            )

        if kind == "uta_financial":
            event_type, direction = _UTA_FINANCIAL_TYPE_MAP[str(payload.get("type"))]
            amount = _positive_amount(payload.get("amount")) or "0"
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"uta:{str(payload.get('_category', '')).lower()}",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=amount,
                source_label=self.account_label,
                notes=f"Bitget {str(payload.get('type', '')).replace('_', ' ').lower()} · {payload.get('id', '')}",
                order_id=payload.get("id"),
            )

        if kind == "uta_convert":
            return NormalizedEvent(
                event_type="BUY",
                event_subtype="convert",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("toCoin", "")).upper(),
                amount=str(payload.get("toCoinSize", "0")),
                source_label=self.account_label,
                notes=f"Bitget convert · {payload.get('fromCoin')} → {payload.get('toCoin')}",
                secondary_asset_symbol=str(payload.get("fromCoin", "")).upper(),
                secondary_amount=_positive_amount(payload.get("fromCoinSize")),
            )

        # futures_bill: a generic account ledger entry. Bitget tags these
        # with a business type (`bizType` / `businessType` depending on
        # product) — map the ones we recognize, leave the rest as UNKNOWN
        # rather than guess (plan §38).
        biz = str(payload.get("businessType") or payload.get("bizType") or "").lower()
        biz_map = {
            "funding_fee": ("FUNDING_PAYMENT", "fee"),
            "contract_settle_fee": ("FUNDING_PAYMENT", "fee"),
            "close_position": ("FUTURES_PNL", "pnl"),
            "open_position": ("FUTURES_OPEN", "open"),
            "trans_from_exchange": ("TRANSFER", "transfer"),
            "trans_to_exchange": ("TRANSFER", "transfer"),
            "liquidation": ("LIQUIDATION", "liquidation"),
        }
        event_type, _ = biz_map.get(biz, ("UNKNOWN", "unrecognized"))
        amount_raw = payload.get("amount", "0")
        return NormalizedEvent(
            event_type=event_type,
            event_subtype=f"futures:{biz or 'unspecified'}",
            direction="+" if not str(amount_raw).startswith("-") else "-",
            status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol=str(payload.get("coin", payload.get("marginCoin", ""))).upper(),
            amount=str(amount_raw).lstrip("-") or "0",
            source_label=self.account_label,
            notes=f"Bitget futures {payload.get('_productType', '')} · {biz or 'unrecognized business type'} · {payload.get('billId', '')}",
            order_id=payload.get("billId"),
        )


def _ms(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


_QUOTE_ASSETS = (
    "USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USDP", "DAI", "BTC", "ETH", "BNB",
    "EUR", "TRY", "BRL", "GBP", "AUD", "RUB", "UAH", "ZAR", "NGN", "PLN", "RON",
    "JPY", "ARS", "MXN", "COP", "IDRT", "BIDR",
)


def _pair_assets(symbol: str) -> tuple[str, str | None]:
    normalized = str(symbol or "").upper()
    for quote in _QUOTE_ASSETS:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)], quote
    return normalized, None


def _base_asset(symbol: str) -> str:
    return _pair_assets(symbol)[0]


def _positive_amount(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if amount == 0:
        return None
    return format(abs(amount), "f")


def _uta_fill_fees(payload: dict) -> list[NormalizedFee]:
    """V3 fills report fees as a flat list of {feeCoin, fee} — simpler than
    the V2 feeDetail shapes _spot_fees has to normalize."""
    fees: list[NormalizedFee] = []
    for entry in payload.get("feeDetail") or []:
        if not isinstance(entry, dict):
            continue
        amount = _positive_amount(entry.get("fee"))
        if amount:
            fees.append(NormalizedFee(fee_type="TRADING_FEE", asset_symbol=str(entry.get("feeCoin", "")).upper(), amount=amount))
    return fees


def _spot_fees(payload: dict) -> list[NormalizedFee]:
    """Normalize both the current object and older list/mapping fee shapes."""
    detail = payload.get("feeDetail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except (TypeError, ValueError):
            detail = None

    entries: list[tuple[str | None, object]] = []
    if isinstance(detail, dict):
        direct_amount = detail.get("totalFee") or detail.get("fee")
        if direct_amount:
            entries.append((detail.get("feeCoin") or detail.get("fee_coin"), direct_amount))
        else:
            for fee_asset, info in detail.items():
                if isinstance(info, dict):
                    entries.append((info.get("feeCoin") or info.get("fee_coin") or fee_asset, info.get("totalFee") or info.get("fee")))
    elif isinstance(detail, list):
        for info in detail:
            if isinstance(info, dict):
                entries.append((info.get("feeCoin") or info.get("fee_coin"), info.get("totalFee") or info.get("fee")))

    base_asset = _base_asset(payload.get("symbol", ""))
    fees: list[NormalizedFee] = []
    for fee_asset, amount in entries:
        normalized_amount = _positive_amount(amount)
        if normalized_amount:
            fees.append(
                NormalizedFee(
                    fee_type="TRADING_FEE",
                    asset_symbol=str(fee_asset or payload.get("feeCoin") or base_asset).upper(),
                    amount=normalized_amount,
                )
            )
    return fees
