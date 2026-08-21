from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlencode

import httpx

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

BASE_URL = "https://api.binance.com"
FUTURES_BASE_URL = "https://fapi.binance.com"

# Binance's spot deposit/withdrawal history endpoints are symbol-agnostic
# (read-only, no key permission beyond "Enable Reading" needed) and stay
# mandatory (raise on failure) — a bad key/IP-restriction/permission issue on
# these should still surface as a clear sync error rather than a quiet
# zero-event sync. Trade history (myTrades) is per-symbol by design on
# Binance's side — there is no "all trades" endpoint — so trades are pulled
# both for symbols the account explicitly configures and for pairs guessed
# from live balances (see _discover_symbols), with each symbol query optional
# so one bad/nonexistent pair doesn't block the rest. Margin/earn/futures/
# convert/fiat each need their own key permission scope, so each is fetched
# independently: a key without that scope enabled just contributes nothing
# for that category instead of failing the whole sync. Anything genuinely
# unreachable on a mandatory call turns into ConnectorUnavailable rather than
# a guess (plan §81).

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

# Binance has no "all trades" endpoint, so myTrades still needs a symbol list —
# but most retail users never fill in the (optional) "Trading pairs" field, and
# a lot of real holdings never touch myTrades/deposit history at all (Convert,
# Buy Crypto with card/bank). To actually "recognize" a user's coins we: (1)
# auto-derive candidate symbols from their live balances so trades get pulled
# even with an empty configured list, and (2) pull Convert and Buy-Crypto
# history, which are common acquisition paths myTrades/deposits never see.
# Kept to the 3 dominant quote assets and a capped, sorted asset list — an
# account with many dust balances could otherwise fan out into hundreds of
# per-symbol calls and trip Binance's request-weight rate limit.
_QUOTE_CANDIDATES = ("USDT", "USDC", "BTC")
_MAX_DISCOVERED_ASSETS = 30

# Binance requires a bounded startTime/endTime window (max 30 days) per call
# for convert history — but unlike Bitget's UTA API, Binance doesn't document
# a hard total retention limit for it, so a deep backfill can chain windows
# back several years instead of stopping at an artificial cutoff. ~36 windows
# for a 3-year backfill is a modest number of extra calls.
_CONVERT_WINDOW_MS = 30 * 24 * 3600 * 1000
_CONVERT_BACKFILL_LOOKBACK_MS = 3 * 365 * 24 * 3600 * 1000


class BinanceLiveConnector:
    source_id = "binance"

    def __init__(self, api_key: str, api_secret: str, account_label: str, symbols: list[str] | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account_label = account_label
        self.symbols = symbols or []

    @property
    def version(self) -> str:
        return "binance-live-0.4"

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

    def _signed_get_optional_list(self, base: str, path: str, params: dict | None = None, *, list_key: str) -> list[dict]:
        """Like _signed_get_optional, but for endpoints that wrap their array
        in an envelope object (e.g. {"list": [...]} or {"data": [...]})
        instead of returning a bare list."""
        try:
            result = self._signed_get(base, path, params)
        except ConnectorUnavailable:
            return []
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get(list_key) or []
        return []

    def _discover_symbols(self) -> list[str]:
        """Guess trading pairs to query myTrades for from live balances, so
        trade history still comes through when the account was connected
        without filling in the (optional) "Trading pairs" field. A guessed
        pair that doesn't exist on Binance just yields nothing for that pair
        (fetch() queries it via _signed_get_optional)."""
        try:
            account = self._signed_get(BASE_URL, "/api/v3/account")
        except ConnectorUnavailable:
            return []
        balances = account.get("balances") if isinstance(account, dict) else None
        if not balances:
            return []
        held: set[str] = set()
        for bal in balances:
            try:
                total = Decimal(str(bal.get("free", "0"))) + Decimal(str(bal.get("locked", "0")))
            except (InvalidOperation, ValueError):
                continue
            if total > 0:
                held.add(str(bal.get("asset", "")).upper())
        return [
            f"{asset}{quote}"
            for asset in sorted(held)[:_MAX_DISCOVERED_ASSETS]
            for quote in _QUOTE_CANDIDATES
            if asset != quote
        ]

    def test_connection(self) -> bool:
        self._signed_get(BASE_URL, "/api/v3/account")
        return True

    def fetch_balances(self) -> Iterable[Balance]:
        account = self._signed_get(BASE_URL, "/api/v3/account")
        balances = account.get("balances", []) if isinstance(account, dict) else []
        result: list[Balance] = []
        for bal in balances:
            try:
                total = Decimal(str(bal.get("free", "0"))) + Decimal(str(bal.get("locked", "0")))
            except (InvalidOperation, ValueError):
                continue
            if total > 0:
                result.append(Balance(str(bal.get("asset", "")).upper(), format(total, "f")))
        return result

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

        configured_symbols = list(self.symbols)
        auto_symbols = [s for s in self._discover_symbols() if s not in configured_symbols]
        for symbol in configured_symbols + auto_symbols:
            trade_params = {"symbol": symbol, "limit": "1000"}
            if since is not None:
                trade_params["startTime"] = str(int(since.timestamp() * 1000))
            # Optional: an invalid/mistyped configured symbol, or a guessed
            # pair that doesn't exist on Binance, should skip that symbol
            # rather than aborting deposits/withdrawals/remaining symbols.
            trades = self._signed_get_optional(BASE_URL, "/api/v3/myTrades", trade_params)
            for trade in trades:
                payload = {**trade, "_kind": "trade", "_symbol": symbol}
                yield RawRecord(self.source_id, f"trade-{trade['id']}", _ms(trade.get("time")), payload)

        # Convert (instant crypto-to-crypto swap) and Buy/Sell Crypto with
        # fiat (card/bank) — common acquisition paths that never show up as a
        # deposit or a myTrades fill, and a frequent reason coins go
        # "unrecognized" for a newly-connected account.
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        conv_start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS

        window_start = conv_start_ms
        while window_start < now_ms:
            window_end = min(window_start + _CONVERT_WINDOW_MS, now_ms)
            conv_params = {"startTime": str(window_start), "endTime": str(window_end), "limit": "1000"}
            for record in self._signed_get_optional_list(BASE_URL, "/sapi/v1/convert/tradeFlow", conv_params, list_key="list"):
                if str(record.get("orderStatus", "")).upper() != "SUCCESS":
                    continue
                payload = {**record, "_kind": "convert"}
                yield RawRecord(self.source_id, f"convert-{record.get('orderId')}", _ms(record.get("createTime")), payload)
            window_start = window_end

        for transaction_type, kind in (("0", "fiat_buy"), ("1", "fiat_sell")):
            page = 1
            while page <= 20:
                fiat_params = {"transactionType": transaction_type, "page": str(page), "rows": "100"}
                items = self._signed_get_optional_list(BASE_URL, "/sapi/v1/fiat/payments", fiat_params, list_key="data")
                if not items:
                    break
                for record in items:
                    if str(record.get("status")) != "Completed":
                        continue
                    payload = {**record, "_kind": kind}
                    yield RawRecord(self.source_id, f"{kind}-{record.get('orderNo')}", _ms(record.get("createTime")), payload)
                if len(items) < 100:
                    break
                page += 1

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

        if kind == "convert":
            return NormalizedEvent(
                event_type="BUY",
                event_subtype="convert",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("toAsset", "")).upper(),
                amount=str(payload.get("toAmount", "0")),
                source_label=self.account_label,
                notes=f"Binance convert · {payload.get('fromAsset')} → {payload.get('toAsset')}",
                secondary_asset_symbol=str(payload.get("fromAsset", "")).upper(),
                secondary_amount=_positive_amount(payload.get("fromAmount")),
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind == "fiat_buy":
            return NormalizedEvent(
                event_type="BUY",
                event_subtype="fiat",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("cryptoCurrency", "")).upper(),
                amount=str(payload.get("obtainAmount", "0")),
                source_label=self.account_label,
                notes=f"Binance buy crypto ({payload.get('paymentMethod', 'fiat')}) · {payload.get('orderNo', '')}",
                fees=_fiat_fees(payload),
                secondary_asset_symbol=str(payload.get("fiatCurrency", "")).upper(),
                secondary_amount=_positive_amount(payload.get("sourceAmount")),
                order_id=payload.get("orderNo"),
            )

        if kind == "fiat_sell":
            return NormalizedEvent(
                event_type="SELL",
                event_subtype="fiat",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("cryptoCurrency", "")).upper(),
                amount=str(payload.get("sourceAmount", "0")),
                source_label=self.account_label,
                notes=f"Binance sell crypto ({payload.get('paymentMethod', 'fiat')}) · {payload.get('orderNo', '')}",
                fees=_fiat_fees(payload),
                secondary_asset_symbol=str(payload.get("fiatCurrency", "")).upper(),
                secondary_amount=_positive_amount(payload.get("obtainAmount")),
                order_id=payload.get("orderNo"),
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


def _fiat_fees(payload: dict) -> list[NormalizedFee]:
    amount = _positive_amount(payload.get("totalFee"))
    if not amount:
        return []
    return [NormalizedFee(fee_type="EXCHANGE_FEE", asset_symbol=str(payload.get("fiatCurrency", "")).upper(), amount=amount)]
