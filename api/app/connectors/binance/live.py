from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlencode

import httpx

from app.connectors.base import ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"

# Binance's spot deposit/withdrawal history endpoints are symbol-agnostic
# (read-only, no key permission beyond "Enable Reading" needed). Trade
# history (myTrades) is per-symbol by design on Binance's side — there is no
# "all trades" endpoint — so we only pull it for symbols the account
# explicitly configures. Margin/earn/futures each need their own key
# permission scope, so each is fetched independently: a key without
# futures/margin enabled just contributes nothing for that category
# instead of failing the whole sync. Anything genuinely unreachable turns
# into ConnectorUnavailable rather than a guess (plan §81).

# Binance futures' income endpoint reports many entry types; only the ones
# relevant to a personal ledger are mapped, everything else stays UNKNOWN
# with full raw evidence retained (plan §38).
_INCOME_TYPE_MAP = {
    "REALIZED_PNL": "FUTURES_PNL",
    "FUNDING_FEE": "FUNDING_PAYMENT",
    "COMMISSION": "TRADING_FEE",
    "INSURANCE_CLEAR": "LIQUIDATION",
    "REFERRAL_KICKBACK": "REFERRAL_REWARD",
    "COMMISSION_REBATE": "CASHBACK",
}


class BinanceLiveConnector:
    source_id = "binance"

    def __init__(self, api_key: str, api_secret: str, account_label: str, symbols: list[str] | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account_label = account_label
        self.symbols = symbols or []

    @property
    def version(self) -> str:
        return "binance-live-0.3"

    def _signed_get(self, base: str, path: str, params: dict | None = None) -> dict | list:
        params = {**(params or {}), "timestamp": str(int(time.time() * 1000)), "recvWindow": "10000"}
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{query}&signature={signature}"
        try:
            response = httpx.get(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=15.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            detail = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            raise ConnectorUnavailable(f"Could not reach Binance: {detail}") from exc

    def _signed_get_optional(self, base: str, path: str, params: dict | None = None) -> list[dict]:
        try:
            result = self._signed_get(base, path, params)
        except ConnectorUnavailable:
            return []
        return result if isinstance(result, list) else []

    def test_connection(self) -> bool:
        self._signed_get(BASE_URL, "/api/v3/account")
        return True

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        params: dict = {"limit": "1000"}
        if since is not None:
            params["startTime"] = str(int(since.timestamp() * 1000))

        deposits = self._signed_get(BASE_URL, "/sapi/v1/capital/deposit/hisrec", params)
        for record in deposits if isinstance(deposits, list) else []:
            payload = {**record, "_kind": "deposit"}
            yield RawRecord(self.source_id, f"deposit-{record.get('id') or record.get('txId')}", _seconds(record.get("insertTime")), payload)

        withdrawals = self._signed_get(BASE_URL, "/sapi/v1/capital/withdraw/history", params)
        for record in withdrawals if isinstance(withdrawals, list) else []:
            payload = {**record, "_kind": "withdrawal"}
            yield RawRecord(self.source_id, f"withdrawal-{record.get('id')}", _seconds(record.get("applyTime")), payload)

        for symbol in self.symbols:
            trade_params = {"symbol": symbol, "limit": "1000"}
            if since is not None:
                trade_params["startTime"] = str(int(since.timestamp() * 1000))
            trades = self._signed_get(BASE_URL, "/api/v3/myTrades", trade_params)
            for trade in trades if isinstance(trades, list) else []:
                payload = {**trade, "_kind": "trade", "_symbol": symbol}
                yield RawRecord(self.source_id, f"trade-{trade['id']}", _ms(trade.get("time")), payload)

        # Margin: classic loan/repay history endpoints.
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/margin/loan", params):
            payload = {**record, "_kind": "margin_borrow"}
            yield RawRecord(self.source_id, f"margin-loan-{record.get('txId')}", _seconds(record.get("timestamp")), payload)
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/margin/repay", params):
            payload = {**record, "_kind": "margin_repay"}
            yield RawRecord(self.source_id, f"margin-repay-{record.get('txId')}", _seconds(record.get("timestamp")), payload)

        # Simple Earn (flexible) subscribe/redeem/reward history.
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/simple-earn/flexible/history/subscriptionRecord", params):
            payload = {**record, "_kind": "earn_subscribe"}
            yield RawRecord(self.source_id, f"earn-sub-{record.get('purchaseId')}", _seconds(record.get("time")), payload)
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/simple-earn/flexible/history/redemptionRecord", params):
            payload = {**record, "_kind": "earn_redeem"}
            yield RawRecord(self.source_id, f"earn-redeem-{record.get('redeemId')}", _seconds(record.get("time")), payload)
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/simple-earn/flexible/history/rewardsRecord", params):
            payload = {**record, "_kind": "earn_reward"}
            yield RawRecord(self.source_id, f"earn-reward-{record.get('time')}-{record.get('asset')}", _seconds(record.get("time")), payload)

        # Airdrops / dividends Binance distributes directly to holders.
        for record in self._signed_get_optional(BASE_URL, "/sapi/v1/asset/assetDividend", params):
            payload = {**record, "_kind": "dividend"}
            yield RawRecord(self.source_id, f"dividend-{record.get('tranId')}", _seconds(record.get("divTime")), payload)

        # Futures (USDT-M) income ledger — covers realized P/L, funding
        # fees, commission, and liquidation-adjacent entries in one call.
        for record in self._signed_get_optional(FUTURES_BASE_URL, "/fapi/v1/income", params):
            payload = {**record, "_kind": "futures_income"}
            yield RawRecord(self.source_id, f"futures-income-{record.get('tranId')}-{record.get('time')}", _ms(record.get("time")), payload)

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        kind = payload["_kind"]

        if kind == "deposit":
            return NormalizedEvent(
                event_type="DEPOSIT",
                event_subtype="exchange",
                direction="+",
                status="COMPLETE" if str(payload.get("status")) in ("1", "6") else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                address_to=payload.get("address"),
                notes=f"Binance deposit · {payload.get('txId', '')[:16]}",
                deposit_id=str(payload.get("id")) if payload.get("id") is not None else None,
                tx_hash=payload.get("txId"),
            )

        if kind == "withdrawal":
            fees = []
            if payload.get("transactionFee"):
                fees.append(NormalizedFee(fee_type="EXCHANGE_FEE", asset_symbol=str(payload.get("coin", "")).upper(), amount=str(payload["transactionFee"])))
            return NormalizedEvent(
                event_type="WITHDRAWAL",
                event_subtype="exchange",
                direction="-",
                status="COMPLETE" if str(payload.get("status")) == "6" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                destination_label=payload.get("address"),
                address_to=payload.get("address"),
                notes=f"Binance withdrawal · {payload.get('id')}",
                fees=fees,
                withdrawal_id=payload.get("id"),
                tx_hash=payload.get("txId"),
            )

        if kind == "trade":
            side = "buy" if payload.get("isBuyer") else "sell"
            base_asset, detected_quote_asset = _pair_assets(payload.get("_symbol", ""))
            quote_asset = str(payload.get("quoteAsset") or detected_quote_asset or "").upper() or None
            fees = []
            if payload.get("commission"):
                fees.append(NormalizedFee(fee_type="TRADING_FEE", asset_symbol=str(payload.get("commissionAsset", "")).upper(), amount=str(payload["commission"])))
            return NormalizedEvent(
                event_type="BUY" if side == "buy" else "SELL",
                event_subtype="spot",
                direction="+" if side == "buy" else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=base_asset,
                amount=str(payload.get("qty", "0")),
                source_label=self.account_label,
                notes=f"Binance spot {side} · {payload['_symbol']}",
                fees=fees,
                secondary_asset_symbol=quote_asset,
                secondary_amount=_positive_amount(payload.get("quoteQty")),
                trade_id=str(payload.get("id")) if payload.get("id") is not None else None,
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind == "margin_borrow":
            return NormalizedEvent(
                event_type="MARGIN_BORROW",
                event_subtype="margin",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("principal", "0")),
                source_label=self.account_label,
                notes=f"Binance margin loan · {payload.get('txId', '')}",
                order_id=str(payload.get("txId")) if payload.get("txId") is not None else None,
            )

        if kind == "margin_repay":
            return NormalizedEvent(
                event_type="MARGIN_REPAY",
                event_subtype="margin",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", payload.get("principal", "0"))),
                source_label=self.account_label,
                notes=f"Binance margin repay · {payload.get('txId', '')}",
                order_id=str(payload.get("txId")) if payload.get("txId") is not None else None,
            )

        if kind in ("earn_subscribe", "earn_redeem", "earn_reward"):
            event_type = {"earn_subscribe": "STAKING_DEPOSIT", "earn_redeem": "STAKING_WITHDRAWAL", "earn_reward": "STAKING_REWARD"}[kind]
            return NormalizedEvent(
                event_type=event_type,
                event_subtype="simple_earn_flexible",
                direction="-" if kind == "earn_subscribe" else "+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                notes=f"Binance Simple Earn ({kind.split('_')[1]})",
            )

        if kind == "dividend":
            return NormalizedEvent(
                event_type="AIRDROP",
                event_subtype="asset_dividend",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                notes=f"Binance asset dividend · {payload.get('enInfo', '')}",
                order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            )

        # futures_income
        income_type = str(payload.get("incomeType", ""))
        event_type = _INCOME_TYPE_MAP.get(income_type, "UNKNOWN")
        amount_raw = str(payload.get("income", "0"))
        return NormalizedEvent(
            event_type=event_type,
            event_subtype=f"futures:{income_type or 'unspecified'}",
            direction="-" if amount_raw.startswith("-") else "+",
            status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol=str(payload.get("asset", "")).upper(),
            amount=amount_raw.lstrip("-") or "0",
            source_label=self.account_label,
            notes=f"Binance futures {income_type or 'unrecognized income type'} · {payload.get('symbol', '')}",
            order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            trade_id=str(payload.get("tradeId")) if payload.get("tradeId") else None,
        )


def _ms(value) -> datetime | None:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc) if value else None


def _seconds(value) -> datetime | None:
    return _ms(value)


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
