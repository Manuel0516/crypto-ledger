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

from app.connectors.base import ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

BASE_URL = "https://api.bitget.com"

# Bitget's V2 API. Read-only endpoints only — this connector never signs a
# trade or withdrawal request (plan §81/§30). Core spot endpoints (fills,
# deposits, withdrawals) are well-exercised; margin/futures/earn use the
# same V2 surface but are lower-traffic paths for this connector, so each
# product category is fetched independently — a permission error or
# endpoint drift on one (e.g. a key without futures access) degrades that
# category to "nothing pulled this round" rather than breaking the rest.


class BitgetLiveConnector:
    source_id = "bitget"

    def __init__(self, api_key: str, api_secret: str, passphrase: str, account_label: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.account_label = account_label

    @property
    def version(self) -> str:
        return "bitget-live-0.3"

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
            raise ConnectorUnavailable(f"Bitget API error {data.get('code')}: {data.get('msg')}")
        return data.get("data") or []

    def _get_optional(self, path: str, params: dict | None = None) -> list[dict]:
        """Like _get, but a failure (missing permission, endpoint drift on a
        less-exercised product) just means this category contributes
        nothing this round instead of aborting the whole sync."""
        try:
            return self._get(path, params)
        except ConnectorUnavailable:
            return []

    def test_connection(self) -> bool:
        self._get("/api/v2/spot/account/info")
        return True

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
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
