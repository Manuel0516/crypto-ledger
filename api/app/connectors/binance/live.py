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
OPTIONS_BASE_URL = "https://eapi.binance.com"

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
_AUTO_INVEST_WINDOW_MS = 30 * 24 * 3600 * 1000
_LOAN_WINDOW_MS = 180 * 24 * 3600 * 1000
_STAKING_WINDOW_MS = 90 * 24 * 3600 * 1000
_MARGIN_INTEREST_WINDOW_MS = 30 * 24 * 3600 * 1000
_MARGIN_INTEREST_RETENTION_MS = 90 * 24 * 3600 * 1000
_BLVT_RETENTION_MS = 90 * 24 * 3600 * 1000
_PAY_WINDOW_MS = 90 * 24 * 3600 * 1000
_PAY_RETENTION_MS = 18 * 30 * 24 * 3600 * 1000
_FUTURES_TRADE_WINDOW_MS = 7 * 24 * 3600 * 1000
_FUTURES_TRADE_RETENTION_MS = 180 * 24 * 3600 * 1000
_UNIVERSAL_TRANSFER_WINDOW_MS = 180 * 24 * 3600 * 1000

# The Universal Transfer history endpoint is route-filtered. Query every
# documented personal-account route; a user can transfer among Spot, Funding,
# Margin, Futures, Options and Portfolio Margin without any of those moves
# appearing in an external deposit/withdrawal history.
_UNIVERSAL_TRANSFER_TYPES = (
    "MAIN_UMFUTURE", "MAIN_CMFUTURE", "MAIN_MARGIN", "UMFUTURE_MAIN", "UMFUTURE_MARGIN",
    "CMFUTURE_MAIN", "CMFUTURE_MARGIN", "MARGIN_MAIN", "MARGIN_UMFUTURE", "MARGIN_CMFUTURE",
    "ISOLATEDMARGIN_MARGIN", "MARGIN_ISOLATEDMARGIN", "ISOLATEDMARGIN_ISOLATEDMARGIN",
    "MAIN_FUNDING", "FUNDING_MAIN", "FUNDING_UMFUTURE", "UMFUTURE_FUNDING", "MARGIN_FUNDING",
    "FUNDING_MARGIN", "FUNDING_CMFUTURE", "CMFUTURE_FUNDING", "MAIN_OPTION", "OPTION_MAIN",
    "UMFUTURE_OPTION", "OPTION_UMFUTURE", "MARGIN_OPTION", "OPTION_MARGIN", "FUNDING_OPTION",
    "OPTION_FUNDING", "MAIN_PORTFOLIO_MARGIN", "PORTFOLIO_MARGIN_MAIN",
)

# Deposit/withdrawal history: Binance's documented default, when neither
# startTime nor endTime is sent, is only the *last 7 days* — not "full
# history" as fetch() previously assumed on a plain backfill call. Anything
# older was silently invisible. Explicit windowing (max 90 days per call,
# per Binance's docs) fixes this the same way convert history already was.
_HISTORY_WINDOW_MS = 90 * 24 * 3600 * 1000
_HISTORY_BACKFILL_LOOKBACK_MS = _CONVERT_BACKFILL_LOOKBACK_MS


class BinanceLiveConnector:
    source_id = "binance"

    def __init__(self, api_key: str, api_secret: str, account_label: str, symbols: list[str] | None = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account_label = account_label
        self.symbols = symbols or []

    @property
    def version(self) -> str:
        return "binance-live-0.12"

    def _signed_get(self, base: str, path: str, params: dict | None = None) -> dict | list:
        params = {**(params or {}), "timestamp": str(int(time.time() * 1000)), "recvWindow": "10000"}
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{query}&signature={signature}"
        try:
            response = httpx.get(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=15.0)
            response.raise_for_status()
            result = response.json()
        except ValueError as exc:
            raise ConnectorUnavailable(f"Binance returned a non-JSON response from {path}") from exc
        except httpx.HTTPError as exc:
            detail = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else str(exc)
            status = f" (HTTP {exc.response.status_code})" if isinstance(exc, httpx.HTTPStatusError) else ""
            raise ConnectorUnavailable(f"Binance request failed for {path}{status}: {detail}") from exc

        # Binance reports many authentication, permission, timestamp and rate
        # limit failures as HTTP 200 with a negative ``code``. Do not let an
        # error envelope fall through as if it were valid history: mandatory
        # calls must stop with the provider's actionable message, while the
        # existing optional-call wrappers can still skip unsupported scopes.
        if isinstance(result, dict):
            code = result.get("code")
            try:
                numeric_code = int(code) if code is not None else None
            except (TypeError, ValueError):
                numeric_code = None
            if numeric_code is not None and numeric_code < 0:
                message = str(result.get("msg") or "unknown Binance error")
                raise ConnectorUnavailable(f"Binance rejected {path} (code {numeric_code}): {message}")

        return result

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

    def _signed_get_optional_object(self, base: str, path: str, params: dict | None = None) -> dict:
        """Optional signed endpoint that returns an envelope object."""
        try:
            result = self._signed_get(base, path, params)
        except ConnectorUnavailable:
            return {}
        return result if isinstance(result, dict) else {}

    def _public_get_optional_object(self, path: str, params: dict | None = None) -> dict:
        """Read a public Binance catalog endpoint without risking sync failure."""
        try:
            response = httpx.get(f"{BASE_URL}{path}", params=params, timeout=15.0)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError):
            return {}
        return result if isinstance(result, dict) else {}

    def _signed_post_optional(self, base: str, path: str, params: dict | None = None) -> dict | list:
        """Best-effort signed POST for read-only Binance endpoints.

        Binance exposes the Funding wallet through POST even though the call
        has no side effects.  Keep it separate from ``_signed_get`` so it is
        impossible for this connector to accidentally reuse a POST helper for
        a trading endpoint.
        """
        request_params = {**(params or {}), "timestamp": str(int(time.time() * 1000)), "recvWindow": "10000"}
        query = urlencode(request_params)
        signature = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{query}&signature={signature}"
        try:
            response = httpx.post(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=15.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return []

    def _current_position_rows(self, path: str) -> Iterable[dict]:
        """Yield every current Simple Earn position from an envelope API."""
        current = 1
        imported = 0
        while True:
            data = self._signed_get_optional_object(BASE_URL, path, {"current": str(current), "size": "100"})
            rows = data.get("rows") or []
            if not isinstance(rows, list):
                return
            records = [row for row in rows if isinstance(row, dict)]
            yield from records
            imported += len(records)
            try:
                has_more = imported < int(data.get("total"))
            except (TypeError, ValueError):
                has_more = len(records) == 100
            if not records or not has_more:
                return
            current += 1

    def _simple_earn_history(self, path: str, since: datetime | None, extra_params: dict | None = None) -> Iterable[dict]:
        """Retrieve every page of a Simple Earn history endpoint.

        Binance returns these records in ``rows``/``total`` envelopes, not a
        bare list.  Treating them as a list (as the earlier connector did)
        silently discarded every Flexible Earn record.  The APIs accept a
        ranged, page-based query; one range also avoids multiplying costly
        Earn requests into dozens of small windows during an initial sync.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        current = 1
        imported = 0
        while True:
            params = {
                **(extra_params or {}),
                "startTime": str(start_ms),
                "endTime": str(now_ms),
                "current": str(current),
                "size": "100",
            }
            data = self._signed_get_optional_object(BASE_URL, path, params)
            rows = data.get("rows") or data.get("list") or []
            if not isinstance(rows, list):
                return
            records = [row for row in rows if isinstance(row, dict)]
            yield from records
            imported += len(records)
            total = data.get("total")
            try:
                has_more = imported < int(total)
            except (TypeError, ValueError):
                has_more = len(records) == 100
            if not records or not has_more:
                return
            current += 1

    def _c2c_history(self, trade_type: str, since: datetime | None) -> Iterable[dict]:
        """Fetch completed Binance C2C/P2P orders with their fiat leg.

        C2C is outside the spot matching engine, so it is never present in
        ``myTrades`` or fiat-card history.  It must be queried separately for
        both order directions.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        window_start = start_ms
        while window_start < now_ms:
            window_end = min(window_start + _AUTO_INVEST_WINDOW_MS, now_ms)
            for page in range(1, 101):
                records = self._signed_get_optional_list(
                    BASE_URL,
                    "/sapi/v1/c2c/orderMatch/listUserOrderHistory",
                    {
                        "tradeType": trade_type,
                        "startTimestamp": str(window_start),
                        "endTimestamp": str(window_end),
                        "page": str(page),
                        "rows": "100",
                    },
                    list_key="data",
                )
                completed = [record for record in records if str(record.get("orderStatus", "")).upper() == "COMPLETED"]
                yield from completed
                if len(records) < 100:
                    break
            window_start = window_end

    def _fiat_history(self, path: str, transaction_type: str, since: datetime | None) -> Iterable[dict]:
        """Page a Fiat endpoint with explicit time bounds.

        Binance otherwise returns only its recent default history. Unlike C2C
        and Pay, Fiat documentation does not impose a maximum interval, so
        retain the requested sync range in one paged query and avoid a
        high-weight fiat-order request per month.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        for page in range(1, 101):
            data = self._signed_get_optional_object(
                BASE_URL,
                path,
                {
                    "transactionType": transaction_type,
                    "beginTime": str(start_ms),
                    "endTime": str(now_ms),
                    "page": str(page),
                    "rows": "500",
                },
            )
            records = data.get("data") or []
            if not isinstance(records, list):
                return
            typed_records = [record for record in records if isinstance(record, dict)]
            yield from typed_records
            total = data.get("total")
            try:
                has_more = page * 500 < int(total)
            except (TypeError, ValueError):
                has_more = len(typed_records) == 500
            if not typed_records or not has_more:
                return

    def _pay_history(self, since: datetime | None) -> Iterable[dict]:
        """Read Binance Pay history inside its 18-month/90-day API limits."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        requested_start = int(since.timestamp() * 1000) if since is not None else now_ms - _PAY_RETENTION_MS
        window_start = max(requested_start, now_ms - _PAY_RETENTION_MS)
        while window_start < now_ms:
            window_end = min(window_start + _PAY_WINDOW_MS, now_ms)
            data = self._signed_get_optional_object(
                BASE_URL,
                "/sapi/v1/pay/transactions",
                {"startTime": str(window_start), "endTime": str(window_end), "limit": "100"},
            )
            records = data.get("data") or []
            if isinstance(records, list):
                yield from (record for record in records if isinstance(record, dict))
            window_start = window_end

    def _rebate_history(self, since: datetime | None) -> Iterable[dict]:
        """Read every documented Spot rebate and referral-kickback record.

        The endpoint has its own nested ``data.data`` pagination envelope and
        is neither part of Spot trades nor Futures income.  It is optional so
        a key that cannot access the rebate ledger does not prevent the rest
        of an account from syncing.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        for page in range(1, 101):
            envelope = self._signed_get_optional_object(
                BASE_URL,
                "/sapi/v1/rebate/taxQuery",
                {"startTime": str(start_ms), "endTime": str(now_ms), "page": str(page)},
            )
            data = envelope.get("data") or {}
            if not isinstance(data, dict):
                return
            records = data.get("data") or []
            if not isinstance(records, list):
                return
            typed_records = [record for record in records if isinstance(record, dict)]
            yield from typed_records
            try:
                has_more = page < int(data.get("totalPageNum"))
            except (TypeError, ValueError):
                has_more = False
            if not typed_records or not has_more:
                return

    def _universal_transfer_history(self, since: datetime | None) -> Iterable[dict]:
        """Read route-filtered Binance transfers in the API's six-month windows.

        Binance requires a wallet route for this history endpoint. Maintaining
        the full documented route list makes movements into Futures, Options
        and Portfolio Margin visible without modeling them as external money
        flows. Unavailable routes/products are optional no-op calls.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        for transfer_type in _UNIVERSAL_TRANSFER_TYPES:
            window_start = start_ms
            while window_start < now_ms:
                window_end = min(window_start + _UNIVERSAL_TRANSFER_WINDOW_MS, now_ms)
                for current in range(1, 101):
                    data = self._signed_get_optional_object(
                        BASE_URL,
                        "/sapi/v1/asset/transfer",
                        {
                            "type": transfer_type,
                            "startTime": str(window_start),
                            "endTime": str(window_end),
                            "current": str(current),
                            "size": "100",
                        },
                    )
                    rows = data.get("rows") or []
                    if not isinstance(rows, list):
                        break
                    records = [record for record in rows if isinstance(record, dict)]
                    yield from records
                    try:
                        has_more = current * 100 < int(data.get("total"))
                    except (TypeError, ValueError):
                        has_more = len(records) == 100
                    if not records or not has_more:
                        break
                window_start = window_end

    def _mining_history(self, since: datetime | None) -> Iterable[tuple[str, dict]]:
        """Discover Binance Pool accounts and page their payout ledgers.

        Mining accounts are discovered from the authenticated account-list API
        for each public algorithm, so this does not need a separately entered
        pool username. The mining API uses ``startDate``/``endDate`` and
        ``pageIndex``/``pageSize`` rather than the wallet history shape.
        """
        catalog = self._public_get_optional_object("/sapi/v1/mining/pub/algoList")
        algorithms = catalog.get("data") or []
        if not isinstance(algorithms, list):
            return
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        for algorithm in algorithms:
            if not isinstance(algorithm, dict):
                continue
            algo = str(algorithm.get("algoName", ""))
            if not algo:
                continue
            # This ledger belongs directly to the API key's mining account
            # and includes referral, refund and rebate adjustments.
            for page_index in range(1, 101):
                data = self._signed_get_optional_object(
                    BASE_URL,
                    "/sapi/v1/mining/payment/uid",
                    {"algo": algo, "startDate": str(start_ms), "endDate": str(now_ms), "pageIndex": str(page_index), "pageSize": "100"},
                ).get("data") or {}
                if not isinstance(data, dict):
                    break
                records = data.get("accountProfits") or []
                if not isinstance(records, list):
                    break
                typed_records = [record for record in records if isinstance(record, dict)]
                for record in typed_records:
                    yield "mining_account_adjustment", {**record, "_mining_algo": algo}
                try:
                    has_more = page_index * 100 < int(data.get("totalNum"))
                except (TypeError, ValueError):
                    has_more = len(typed_records) == 100
                if not typed_records or not has_more:
                    break

            accounts = self._signed_get_optional_object(
                BASE_URL, "/sapi/v1/mining/statistics/user/list", {"algo": algo}
            ).get("data") or []
            if not isinstance(accounts, list):
                continue
            for account in accounts:
                if not isinstance(account, dict) or not account.get("userName"):
                    continue
                username = str(account["userName"])
                for kind, path, row_key in (
                    ("mining_earning", "/sapi/v1/mining/payment/list", "accountProfits"),
                    ("mining_bonus", "/sapi/v1/mining/payment/other", "otherProfits"),
                ):
                    for page_index in range(1, 101):
                        data = self._signed_get_optional_object(
                            BASE_URL,
                            path,
                            {
                                "algo": algo,
                                "userName": username,
                                "startDate": str(start_ms),
                                "endDate": str(now_ms),
                                "pageIndex": str(page_index),
                                "pageSize": "100",
                            },
                        ).get("data") or {}
                        if not isinstance(data, dict):
                            break
                        records = data.get(row_key) or []
                        if not isinstance(records, list):
                            break
                        typed_records = [record for record in records if isinstance(record, dict)]
                        for record in typed_records:
                            yield kind, {**record, "_mining_algo": algo, "_mining_account": username}
                        try:
                            has_more = page_index * 100 < int(data.get("totalNum"))
                        except (TypeError, ValueError):
                            has_more = len(typed_records) == 100
                        if not typed_records or not has_more:
                            break

    def _futures_symbols(self, base: str, position_path: str) -> set[str]:
        """Discover active derivatives contracts without a user-maintained list."""
        rows = self._signed_get_optional(base, position_path)
        return {
            str(row.get("symbol", "")).upper()
            for row in rows
            if isinstance(row, dict) and row.get("symbol")
        }

    def _futures_trades(self, base: str, path: str, symbol: str, since: datetime | None) -> Iterable[dict]:
        """Fetch a contract's fills within Binance's 6-month/7-day limits.

        The API requires one symbol and caps a time range at seven days. A
        normal range contains fewer than 1,000 fills; when it is full, keep
        following the documented ``fromId`` cursor to avoid silently losing
        high-frequency account activity.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        requested_start = int(since.timestamp() * 1000) if since is not None else now_ms - _FUTURES_TRADE_RETENTION_MS
        window_start = max(requested_start, now_ms - _FUTURES_TRADE_RETENTION_MS)
        while window_start < now_ms:
            window_end = min(window_start + _FUTURES_TRADE_WINDOW_MS, now_ms)
            records = self._signed_get_optional(
                base,
                path,
                {"symbol": symbol, "startTime": str(window_start), "endTime": str(window_end), "limit": "1000"},
            )
            yield from records
            if len(records) == 1000:
                try:
                    next_id = int(records[-1]["id"]) + 1
                except (IndexError, KeyError, TypeError, ValueError):
                    next_id = None
                seen_ids: set[int] = set()
                while next_id is not None and next_id not in seen_ids:
                    seen_ids.add(next_id)
                    cursor_records = self._signed_get_optional(
                        base,
                        path,
                        {"symbol": symbol, "fromId": str(next_id), "limit": "1000"},
                    )
                    if not cursor_records:
                        break
                    in_window = [
                        record for record in cursor_records
                        if window_start <= int(record.get("time", 0)) <= window_end
                    ]
                    yield from in_window
                    try:
                        next_id = int(cursor_records[-1]["id"]) + 1
                        reached_window_end = int(cursor_records[-1].get("time", 0)) >= window_end
                    except (IndexError, KeyError, TypeError, ValueError):
                        break
                    if len(cursor_records) < 1000 or reached_window_end:
                        break
            window_start = window_end

    def _staking_history(self, path: str, since: datetime | None) -> Iterable[dict]:
        """Read the paged, max-three-month history windows used by Binance
        staking APIs.  Unlike Simple Earn, these endpoints reject an overly
        broad time range, so a full backfill must be split explicitly."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        window_start = start_ms
        while window_start < now_ms:
            window_end = min(window_start + _STAKING_WINDOW_MS, now_ms)
            current = 1
            imported = 0
            while True:
                data = self._signed_get_optional_object(
                    BASE_URL,
                    path,
                    {
                        "startTime": str(window_start),
                        "endTime": str(window_end),
                        "current": str(current),
                        "size": "100",
                    },
                )
                rows = data.get("rows") or []
                if not isinstance(rows, list):
                    break
                records = [row for row in rows if isinstance(row, dict)]
                yield from records
                imported += len(records)
                try:
                    has_more = imported < int(data.get("total"))
                except (TypeError, ValueError):
                    has_more = len(records) == 100
                if not records or not has_more:
                    break
                current += 1
            window_start = window_end

    def _auto_invest_history(self, since: datetime | None) -> Iterable[dict]:
        """Read all Binance Auto-Invest subscription executions.

        This product has a separate 30-day maximum query range and paged
        response.  It is not a spot fill: the source asset, target asset,
        execution price and fee live exclusively in this history endpoint.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        window_start = start_ms
        while window_start < now_ms:
            window_end = min(window_start + _AUTO_INVEST_WINDOW_MS, now_ms)
            current = 1
            imported = 0
            while True:
                data = self._signed_get_optional_object(
                    BASE_URL,
                    "/sapi/v1/lending/auto-invest/history/list",
                    {
                        "startTime": str(window_start),
                        "endTime": str(window_end),
                        "current": str(current),
                        "size": "100",
                    },
                )
                rows = data.get("rows") or data.get("list") or data.get("data") or []
                if not isinstance(rows, list):
                    break
                records = [row for row in rows if isinstance(row, dict)]
                yield from records
                imported += len(records)
                try:
                    has_more = imported < int(data.get("total"))
                except (TypeError, ValueError):
                    has_more = len(records) == 100
                if not records or not has_more:
                    break
                current += 1
            window_start = window_end

    def _crypto_loan_history(self, path: str, since: datetime | None) -> Iterable[dict]:
        """Read Binance stable-rate Crypto Loan history in its documented
        180-day windows, consuming every rows/total page."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CONVERT_BACKFILL_LOOKBACK_MS
        window_start = start_ms
        while window_start < now_ms:
            window_end = min(window_start + _LOAN_WINDOW_MS, now_ms)
            current = 1
            imported = 0
            while True:
                data = self._signed_get_optional_object(
                    BASE_URL,
                    path,
                    {
                        "startTime": str(window_start),
                        "endTime": str(window_end),
                        "current": str(current),
                        "limit": "100",
                    },
                )
                rows = data.get("rows") or []
                if not isinstance(rows, list):
                    break
                records = [row for row in rows if isinstance(row, dict)]
                yield from records
                imported += len(records)
                try:
                    has_more = imported < int(data.get("total"))
                except (TypeError, ValueError):
                    has_more = len(records) == 100
                if not records or not has_more:
                    break
                current += 1
            window_start = window_end

    def _blvt_history(self, path: str, since: datetime | None) -> Iterable[dict]:
        """Read Binance Leveraged Token history within its 90-day retention.

        BLVT subscription and redemption records are returned as a bare list
        in Binance's USER_DATA API.  They contain the executed token amount,
        USDT charge/proceeds and fee, so importing them as opaque wallet
        transfers would lose the trade's taxable two-leg context.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        requested_start = int(since.timestamp() * 1000) if since is not None else now_ms - _BLVT_RETENTION_MS
        start_ms = max(requested_start, now_ms - _BLVT_RETENTION_MS)
        yield from self._signed_get_optional(
            BASE_URL,
            path,
            {"startTime": str(start_ms), "endTime": str(now_ms), "limit": "1000"},
        )

    def _margin_interest_history(self, since: datetime | None) -> Iterable[dict]:
        """Read Binance's paged margin-interest ledger without asking the
        endpoint for history it cannot serve.

        Binance limits this endpoint to the most recent 90 days and each
        request to a 30-day range.  Interest rows are returned newest-first
        in a ``rows``/``total`` envelope, so page through every range rather
        than silently keeping only the default first ten charges.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        requested_start = int(since.timestamp() * 1000) if since is not None else now_ms - _MARGIN_INTEREST_RETENTION_MS
        window_start = max(requested_start, now_ms - _MARGIN_INTEREST_RETENTION_MS)
        while window_start < now_ms:
            window_end = min(window_start + _MARGIN_INTEREST_WINDOW_MS, now_ms)
            current = 1
            imported = 0
            while True:
                data = self._signed_get_optional_object(
                    BASE_URL,
                    "/sapi/v1/margin/interestHistory",
                    {
                        "startTime": str(window_start),
                        "endTime": str(window_end),
                        "current": str(current),
                        "size": "100",
                    },
                )
                rows = data.get("rows") or []
                if not isinstance(rows, list):
                    break
                records = [row for row in rows if isinstance(row, dict)]
                yield from records
                imported += len(records)
                try:
                    has_more = imported < int(data.get("total"))
                except (TypeError, ValueError):
                    has_more = len(records) == 100
                if not records or not has_more:
                    break
                current += 1
            window_start = window_end

    def _windowed_history(self, base: str, path: str, since: datetime | None, extra_params: dict | None = None, *, optional: bool = False) -> Iterable[dict]:
        """Pages a Binance history endpoint across explicit ≤90-day windows
        spanning [since or a deep backfill lookback, now) — required because
        Binance defaults to only the last 7 days when no time bounds are
        given at all (confirmed for deposit/withdraw history; applied
        uniformly here since every endpoint below is prone to the same
        silent-data-loss shape if it behaves similarly).

        optional=False (deposits/withdrawals): the first window is
        mandatory — a real credential/permission problem should still
        surface as a clear sync error — but later windows degrade to
        "nothing more from this endpoint" on failure, so one transient
        error doesn't lose everything already fetched from earlier windows.
        optional=True (margin/earn/dividend/futures income): every window
        is optional, matching these categories' existing "a key without
        this scope just contributes nothing" behavior."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _HISTORY_BACKFILL_LOOKBACK_MS
        window_start = start_ms
        first = True
        while window_start < now_ms:
            window_end = min(window_start + _HISTORY_WINDOW_MS, now_ms)
            params = {**(extra_params or {}), "limit": "1000", "startTime": str(window_start), "endTime": str(window_end)}
            if first and not optional:
                result = self._signed_get(base, path, params)
            else:
                result = self._signed_get_optional(base, path, params)
            first = False
            yield from (result if isinstance(result, list) else [])
            window_start = window_end

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

    @staticmethod
    def _symbols_from_assets(assets: Iterable[str]) -> list[str]:
        """Build candidate spot pairs from assets evidenced in historical
        deposits/withdrawals, not just coins still held today.  A user who
        bought and later sold an asset should not lose that trade history
        merely because their live balance is now zero."""
        return [
            f"{asset}{quote}"
            for asset in sorted({str(asset).upper() for asset in assets if asset})[:_MAX_DISCOVERED_ASSETS]
            for quote in _QUOTE_CANDIDATES
            if asset != quote
        ]

    def _spot_trades(self, symbol: str, since: datetime | None) -> Iterable[dict]:
        """Walk Binance's per-symbol ``myTrades`` cursor until exhausted.

        Binance intentionally has no all-symbol account-trade endpoint. The
        first query starts at the sync/backfill timestamp; later requests use
        the documented ``fromId`` cursor so a busy pair is not capped at the
        endpoint's first 1,000 fills.
        """
        start_ms = int(since.timestamp() * 1000) if since is not None else int(datetime.now(timezone.utc).timestamp() * 1000) - _CONVERT_BACKFILL_LOOKBACK_MS
        params: dict[str, str] = {"symbol": symbol, "limit": "1000", "startTime": str(start_ms)}
        next_id: int | None = None
        seen_ids: set[int] = set()
        while True:
            trades = self._signed_get_optional(BASE_URL, "/api/v3/myTrades", params)
            yield from trades
            if len(trades) < 1000:
                return
            try:
                last_id = int(trades[-1]["id"])
            except (IndexError, KeyError, TypeError, ValueError):
                return
            if last_id in seen_ids or (next_id is not None and last_id < next_id):
                return
            seen_ids.add(last_id)
            next_id = last_id + 1
            params = {"symbol": symbol, "limit": "1000", "fromId": str(next_id)}

    def _margin_trades(self, symbol: str, since: datetime | None, *, isolated: bool) -> Iterable[dict]:
        """Walk cross or isolated Margin fills for one symbol.

        Margin executions live in a different ledger from Spot fills.  Query
        both account modes explicitly: leaving ``isIsolated`` absent only
        returns cross-margin trades and makes an isolated-Margin portfolio
        look like it has borrow/repay activity without the trades that used
        those borrowed assets.
        """
        start_ms = int(since.timestamp() * 1000) if since is not None else int(datetime.now(timezone.utc).timestamp() * 1000) - _CONVERT_BACKFILL_LOOKBACK_MS
        params: dict[str, str] = {
            "symbol": symbol,
            "isIsolated": "TRUE" if isolated else "FALSE",
            "limit": "1000",
            "startTime": str(start_ms),
        }
        next_id: int | None = None
        seen_ids: set[int] = set()
        while True:
            trades = self._signed_get_optional(BASE_URL, "/sapi/v1/margin/myTrades", params)
            yield from trades
            if len(trades) < 1000:
                return
            try:
                last_id = int(trades[-1]["id"])
            except (IndexError, KeyError, TypeError, ValueError):
                return
            if last_id in seen_ids or (next_id is not None and last_id < next_id):
                return
            seen_ids.add(last_id)
            next_id = last_id + 1
            params = {
                "symbol": symbol,
                "isIsolated": "TRUE" if isolated else "FALSE",
                "limit": "1000",
                "fromId": str(next_id),
            }

    def test_connection(self) -> bool:
        self._signed_get(BASE_URL, "/api/v3/account")
        return True

    def fetch_balances(self) -> Iterable[Balance]:
        """Aggregate the user-visible Binance wallets this read-only API
        connection can query.  The prior implementation only used the Spot
        account, so Funding, Simple Earn, Margin and Futures holdings made a
        healthy account appear to be missing coins during reconciliation.

        Each extra wallet is best-effort: Binance enables these API families
        independently and an account that has never used one may reject its
        endpoint.  Spot remains the mandatory connection check, while a
        denied optional wallet cannot hide the holdings we can read.
        """
        account = self._signed_get(BASE_URL, "/api/v3/account")
        balances = account.get("balances", []) if isinstance(account, dict) else []
        totals: dict[str, Decimal] = {}

        def add(symbol: object, amount: object) -> None:
            symbol_text = str(symbol or "").upper()
            if not symbol_text:
                return
            try:
                value = Decimal(str(amount))
            except (InvalidOperation, ValueError):
                return
            if value:
                totals[symbol_text] = totals.get(symbol_text, Decimal("0")) + value

        for bal in balances:
            try:
                total = Decimal(str(bal.get("free", "0"))) + Decimal(str(bal.get("locked", "0")))
            except (InvalidOperation, ValueError):
                continue
            add(bal.get("asset"), total)

        # Funding covers Pay, Card, Gift Card and C2C settlement balances.
        funding = self._signed_post_optional(BASE_URL, "/sapi/v1/asset/get-funding-asset")
        for bal in funding if isinstance(funding, list) else []:
            if not isinstance(bal, dict):
                continue
            try:
                total = sum(Decimal(str(bal.get(field, "0"))) for field in ("free", "locked", "freeze", "withdrawing"))
            except (InvalidOperation, ValueError):
                continue
            add(bal.get("asset"), total)

        # Cross-margin's net asset is the actual wallet holding after its
        # borrow and accrued interest, so do not add free/locked/borrowed
        # independently (that would double count a liability).
        margin = self._signed_get_optional_object(BASE_URL, "/sapi/v1/margin/account")
        for bal in margin.get("userAssets", []) if isinstance(margin.get("userAssets"), list) else []:
            if isinstance(bal, dict):
                add(bal.get("asset"), bal.get("netAsset"))

        for base, path in ((FUTURES_BASE_URL, "/fapi/v2/balance"), ("https://dapi.binance.com", "/dapi/v1/balance")):
            futures = self._signed_get_optional(base, path)
            for bal in futures:
                add(bal.get("asset"), bal.get("balance"))

        # Options keeps collateral in a separate margin account. Equity is
        # the documented account balance after realized and unrealized option
        # P/L, so use it for reconciliation rather than an available-only
        # amount that would make locked collateral appear missing.
        options = self._signed_get_optional_object(OPTIONS_BASE_URL, "/eapi/v1/marginAccount")
        for bal in options.get("asset", []) if isinstance(options.get("asset"), list) else []:
            if isinstance(bal, dict):
                add(bal.get("asset"), bal.get("equity"))

        # BFUSD and RWUSD are distinct Binance Earn products, not Spot or
        # the older Flexible/Locked Simple Earn positions.
        bfusd = self._signed_get_optional_object(BASE_URL, "/sapi/v1/bfusd/account")
        add("BFUSD", bfusd.get("bfusdAmount"))
        rwusd = self._signed_get_optional_object(BASE_URL, "/sapi/v1/rwusd/account")
        add("RWUSD", rwusd.get("rwusdAmount"))

        for path, amount_key in (
            ("/sapi/v1/simple-earn/flexible/position", "totalAmount"),
            ("/sapi/v1/simple-earn/locked/position", "amount"),
        ):
            for position in self._current_position_rows(path):
                add(position.get("asset"), position.get(amount_key))

        return [Balance(symbol, format(amount, "f")) for symbol, amount in sorted(totals.items()) if amount > 0]

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        # Buffer these mandatory, bounded histories first. Besides retaining
        # their own evidence, their asset symbols let us discover old trading
        # pairs for assets that are no longer present in the live balance.
        deposits = list(self._windowed_history(BASE_URL, "/sapi/v1/capital/deposit/hisrec", since))
        withdrawals = list(self._windowed_history(BASE_URL, "/sapi/v1/capital/withdraw/history", since))
        historical_assets = {
            str(record.get("coin", "")).upper() for record in (*deposits, *withdrawals) if record.get("coin")
        }

        for record in deposits:
            payload = {**record, "_kind": "deposit"}
            yield RawRecord(self.source_id, f"deposit-{record.get('id') or record.get('txId')}", _seconds(record.get("insertTime")), payload)

        for record in withdrawals:
            payload = {**record, "_kind": "withdrawal"}
            yield RawRecord(self.source_id, f"withdrawal-{record.get('id')}", _seconds(record.get("applyTime")), payload)

        configured_symbols = list(self.symbols)
        auto_symbols = list(
            dict.fromkeys(
                symbol
                for symbol in (*self._discover_symbols(), *self._symbols_from_assets(historical_assets))
                if symbol not in configured_symbols
            )
        )
        for symbol in configured_symbols + auto_symbols:
            # Optional: an invalid/mistyped configured symbol, or a guessed
            # pair that doesn't exist on Binance, should skip that symbol
            # rather than aborting deposits/withdrawals/remaining symbols.
            for trade in self._spot_trades(symbol, since):
                payload = {**trade, "_kind": "trade", "_symbol": symbol}
                yield RawRecord(self.source_id, f"trade-{trade['id']}", _ms(trade.get("time")), payload)

        # Margin trades are separate from Spot's ``myTrades`` endpoint. Use
        # the same discovered symbols so users need not manually enumerate
        # every pair they once traded; an unavailable/invalid symbol remains
        # an optional per-pair miss rather than aborting the exchange sync.
        for symbol in configured_symbols + auto_symbols:
            for isolated in (False, True):
                for trade in self._margin_trades(symbol, since, isolated=isolated):
                    payload = {**trade, "_kind": "margin_trade", "_symbol": symbol, "_is_isolated": isolated}
                    scope = "isolated" if isolated else "cross"
                    yield RawRecord(self.source_id, f"margin-trade-{scope}-{symbol}-{trade['id']}", _ms(trade.get("time")), payload)

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

        # Binance Leveraged Tokens (BLVT) settle outside spot fills.  Their
        # dedicated histories preserve the actual USDT charge/proceeds and
        # product fee for the most recent 90 days Binance makes available.
        for kind, path in (
            ("blvt_subscribe", "/sapi/v1/blvt/subscribe/record"),
            ("blvt_redeem", "/sapi/v1/blvt/redeem/record"),
        ):
            for record in self._blvt_history(path, since):
                payload = {**record, "_kind": kind}
                yield RawRecord(
                    self.source_id,
                    f"{kind}-{record.get('id')}",
                    _ms(record.get("timestamp")),
                    payload,
                )

        for transaction_type, kind in (("0", "fiat_buy"), ("1", "fiat_sell")):
            for record in self._fiat_history("/sapi/v1/fiat/payments", transaction_type, since):
                if str(record.get("status")) != "Completed":
                    continue
                payload = {**record, "_kind": kind}
                yield RawRecord(self.source_id, f"{kind}-{record.get('orderNo')}", _ms(record.get("createTime")), payload)

        # Fiat deposits and withdrawals are separate from buy/sell-card
        # payments and do not appear in the crypto deposit/withdraw ledger.
        for transaction_type, kind in (("0", "fiat_deposit"), ("1", "fiat_withdrawal")):
            for record in self._fiat_history("/sapi/v1/fiat/orders", transaction_type, since):
                payload = {**record, "_kind": kind}
                yield RawRecord(self.source_id, f"{kind}-{record.get('orderNo')}", _ms(record.get("createTime")), payload)

        # Pay keeps its own funding-wallet ledger. C2B payments and refunds
        # have a documented direction; ambiguous C2C/crypto-box records are
        # retained as reviewable evidence rather than discarded.
        for record in self._pay_history(since):
            payload = {**record, "_kind": "pay"}
            yield RawRecord(
                self.source_id,
                f"pay-{record.get('transactionId')}",
                _ms(record.get("transactionTime")),
                payload,
            )

        # Spot commission rebates and referral kickbacks settle directly in
        # the wallet, outside both trade fills and Futures income.  Their API
        # does not expose a per-row ID, so retain its complete tuple as a
        # deterministic evidence key.
        for record in self._rebate_history(since):
            payload = {**record, "_kind": "spot_rebate"}
            record_id = "-".join(
                str(record.get(field, "")) for field in ("type", "asset", "amount", "updateTime")
            )
            yield RawRecord(
                self.source_id,
                f"spot-rebate-{record_id}",
                _ms(record.get("updateTime")),
                payload,
            )

        # A Universal Transfer moves funds between this Binance account's own
        # wallets. Keep it visible for audit/reconciliation, but its matching
        # same-asset legs ensure it is balance- and tax-neutral.
        for record in self._universal_transfer_history(since):
            payload = {**record, "_kind": "universal_transfer"}
            yield RawRecord(
                self.source_id,
                f"universal-transfer-{record.get('tranId')}",
                _ms(record.get("timestamp")),
                payload,
            )

        # Binance Pool is a distinct product ledger. It is queried only when
        # the key can discover mining accounts; ordinary exchange accounts
        # simply produce no records here.
        for kind, record in self._mining_history(since):
            payload = {**record, "_kind": kind}
            record_id = "-".join(
                str(record.get(field, ""))
                for field in ("_mining_algo", "_mining_account", "time", "coinName", "type", "profitAmount", "amount", "puid", "subName")
            )
            yield RawRecord(self.source_id, f"{kind}-{record_id}", _ms(record.get("time")), payload)

        # C2C/P2P trades are settled directly between an external wallet and the
        # user's funding wallet; they do not appear in either spot fills or
        # card/bank "Buy Crypto" history.
        for trade_type in ("BUY", "SELL"):
            for record in self._c2c_history(trade_type, since):
                payload = {**record, "_kind": "c2c"}
                yield RawRecord(self.source_id, f"c2c-{record.get('orderNumber')}", _ms(record.get("createTime")), payload)

        # Auto-Invest executes periodic two-asset purchases outside the Spot
        # matching engine. Its own execution history is the only source that
        # supplies the source wallet/amount, target amount and fee together.
        for record in self._auto_invest_history(since):
            payload = {**record, "_kind": "auto_invest"}
            record_id = record.get("id") or record.get("transactionId")
            yield RawRecord(
                self.source_id,
                f"auto-invest-{record_id}",
                _ms(record.get("transactionDateTime") or record.get("time")),
                payload,
            )

        # Margin: classic loan/repay history endpoints.
        for record in self._windowed_history(BASE_URL, "/sapi/v1/margin/loan", since, optional=True):
            payload = {**record, "_kind": "margin_borrow"}
            yield RawRecord(self.source_id, f"margin-loan-{record.get('txId')}", _seconds(record.get("timestamp")), payload)
        for record in self._windowed_history(BASE_URL, "/sapi/v1/margin/repay", since, optional=True):
            payload = {**record, "_kind": "margin_repay"}
            yield RawRecord(self.source_id, f"margin-repay-{record.get('txId')}", _seconds(record.get("timestamp")), payload)
        for record in self._margin_interest_history(since):
            payload = {**record, "_kind": "margin_interest"}
            interest_id = record.get("txId") or f"{record.get('interestAccuredTime')}-{record.get('asset')}-{record.get('type')}"
            yield RawRecord(
                self.source_id,
                f"margin-interest-{interest_id}",
                _seconds(record.get("interestAccuredTime")),
                payload,
            )

        # Stable-rate Crypto Loans are a distinct product from Margin. The
        # history API supplies the loan's locked collateral and subsequent
        # release/adjustment, which a generic margin ledger cannot recover.
        for record in self._crypto_loan_history("/sapi/v1/loan/borrow/history", since):
            payload = {**record, "_kind": "crypto_loan_borrow"}
            yield RawRecord(self.source_id, f"crypto-loan-borrow-{record.get('orderId')}", _ms(record.get("borrowTime")), payload)
        for record in self._crypto_loan_history("/sapi/v1/loan/repay/history", since):
            record_id = f"{record.get('orderId')}-{record.get('repayTime')}"
            payload = {**record, "_kind": "crypto_loan_repay"}
            yield RawRecord(self.source_id, f"crypto-loan-repay-{record_id}", _ms(record.get("repayTime")), payload)
        for record in self._crypto_loan_history("/sapi/v1/loan/ltv/adjustment/history", since):
            record_id = f"{record.get('orderId')}-{record.get('adjustTime')}"
            payload = {**record, "_kind": "crypto_loan_adjustment"}
            yield RawRecord(self.source_id, f"crypto-loan-adjustment-{record_id}", _ms(record.get("adjustTime")), payload)

        # Flexible Loans use a newer, separately-versioned API. Its history
        # payloads omit order IDs, so use their documented timestamp and
        # asset pair as a stable source key rather than collapsing all rows
        # into one synthetic record.
        for record in self._crypto_loan_history("/sapi/v2/loan/flexible/borrow/history", since):
            record_id = f"{record.get('borrowTime')}-{record.get('loanCoin')}-{record.get('collateralCoin')}"
            payload = {**record, "_kind": "crypto_loan_borrow", "_loan_mode": "flexible"}
            yield RawRecord(self.source_id, f"flexible-loan-borrow-{record_id}", _ms(record.get("borrowTime")), payload)
        for record in self._crypto_loan_history("/sapi/v2/loan/flexible/repay/history", since):
            record_id = f"{record.get('repayTime')}-{record.get('loanCoin')}-{record.get('collateralCoin')}"
            payload = {**record, "_kind": "crypto_loan_repay", "_loan_mode": "flexible"}
            yield RawRecord(self.source_id, f"flexible-loan-repay-{record_id}", _ms(record.get("repayTime")), payload)
        for record in self._crypto_loan_history("/sapi/v2/loan/flexible/ltv/adjustment/history", since):
            record_id = f"{record.get('adjustTime')}-{record.get('loanCoin')}-{record.get('collateralCoin')}"
            payload = {**record, "_kind": "crypto_loan_adjustment", "_loan_mode": "flexible"}
            yield RawRecord(self.source_id, f"flexible-loan-adjustment-{record_id}", _ms(record.get("adjustTime")), payload)
        for record in self._crypto_loan_history("/sapi/v2/loan/flexible/liquidation/history", since):
            record_id = f"{record.get('liquidationStartingTime')}-{record.get('loanCoin')}-{record.get('collateralCoin')}"
            payload = {**record, "_kind": "crypto_loan_liquidation", "_loan_mode": "flexible"}
            yield RawRecord(self.source_id, f"flexible-loan-liquidation-{record_id}", _ms(record.get("liquidationStartingTime")), payload)

        # Simple Earn has both Flexible and Locked products. The endpoints
        # return paged rows/total envelopes (not a bare array), so consume
        # every page and retain each product's original identifiers.
        for product in ("flexible", "locked"):
            prefix = f"/sapi/v1/simple-earn/{product}/history"
            id_prefix = "earn" if product == "flexible" else "earn-locked"
            for record in self._simple_earn_history(f"{prefix}/subscriptionRecord", since):
                payload = {**record, "_kind": "earn_subscribe", "_earn_product": product}
                yield RawRecord(self.source_id, f"{id_prefix}-sub-{record.get('purchaseId')}", _seconds(record.get("time")), payload)
            for record in self._simple_earn_history(f"{prefix}/redemptionRecord", since):
                payload = {**record, "_kind": "earn_redeem", "_earn_product": product}
                yield RawRecord(self.source_id, f"{id_prefix}-redeem-{record.get('redeemId')}", _seconds(record.get("time")), payload)
            for record in self._simple_earn_history(f"{prefix}/rewardsRecord", since, {"type": "ALL"} if product == "flexible" else None):
                payload = {**record, "_kind": "earn_reward", "_earn_product": product}
                reward_id = f"{record.get('time')}-{record.get('asset')}-{record.get('positionId', record.get('type', 'reward'))}"
                # Keep the pre-existing Flexible IDs stable so this update
                # cannot duplicate records already synced by older versions.
                external_id = (
                    f"earn-reward-{record.get('time')}-{record.get('asset')}"
                    if product == "flexible"
                    else f"{id_prefix}-reward-{reward_id}"
                )
                yield RawRecord(self.source_id, external_id, _seconds(record.get("time")), payload)

        # BFUSD and RWUSD have independent histories even though Binance
        # groups their API documentation with Simple Earn. Their records
        # include both source and receipt asset amounts, so retain them as
        # actual exchanges rather than one-sided generic staking movements.
        for product in ("bfusd", "rwusd"):
            prefix = f"/sapi/v1/{product}/history"
            for kind, suffix in (
                ("yield_subscribe", "subscriptionHistory"),
                ("yield_redeem", "redemptionHistory"),
                ("yield_reward", "rewardsHistory"),
            ):
                for record in self._simple_earn_history(f"{prefix}/{suffix}", since):
                    payload = {**record, "_kind": kind, "_yield_product": product}
                    record_id = "-".join(
                        str(record.get(field, ""))
                        for field in ("time", "asset", "amount", "receiveAsset", "receiveAmount", "rewardsAmount")
                    )
                    yield RawRecord(
                        self.source_id,
                        f"{product}-{kind}-{record_id}",
                        _seconds(record.get("time")),
                        payload,
                    )

        # ETH staking issues BETH/WBETH and has its own product ledger;
        # these records do not appear in either Simple Earn or spot fills.
        for record in self._staking_history("/sapi/v1/eth-staking/eth/history/stakingHistory", since):
            payload = {**record, "_kind": "eth_stake"}
            yield RawRecord(self.source_id, f"eth-stake-{record.get('purchaseId')}", _seconds(record.get("time")), payload)
        for record in self._staking_history("/sapi/v1/eth-staking/eth/history/redemptionHistory", since):
            payload = {**record, "_kind": "eth_redeem"}
            yield RawRecord(self.source_id, f"eth-redeem-{record.get('redeemId')}", _seconds(record.get("time")), payload)
        # Wrapping BETH into transferable WBETH (and unwrapping it) is a
        # distinct exchange conversion, not a reward or a spot fill. Both
        # endpoints provide the two asset amounts and their exchange rate.
        for kind, path in (
            ("wbeth_wrap", "/sapi/v1/eth-staking/wbeth/history/wrapHistory"),
            ("wbeth_unwrap", "/sapi/v1/eth-staking/wbeth/history/unwrapHistory"),
        ):
            for record in self._staking_history(path, since):
                payload = {**record, "_kind": kind}
                record_id = f"{record.get('time')}-{record.get('fromAsset')}-{record.get('fromAmount')}-{record.get('toAsset')}-{record.get('toAmount')}"
                yield RawRecord(self.source_id, f"{kind}-{record_id}", _seconds(record.get("time")), payload)
        for reward_kind, path in (
            ("beth_reward", "/sapi/v1/eth-staking/eth/history/rewardsHistory"),
            ("wbeth_reward", "/sapi/v1/eth-staking/eth/history/wbethRewardsHistory"),
        ):
            for record in self._staking_history(path, since):
                payload = {**record, "_kind": reward_kind}
                reward_id = f"{record.get('time')}-{record.get('asset')}-{record.get('amount')}"
                yield RawRecord(self.source_id, f"{reward_kind}-{reward_id}", _seconds(record.get("time")), payload)

        # SOL staking mints BNSOL rather than using the Simple Earn ledger,
        # so deposits, redemptions and accrued SOL-value rewards need their
        # own endpoints just like the ETH/BETH/WBETH product above.
        for record in self._staking_history("/sapi/v1/sol-staking/sol/history/stakingHistory", since):
            payload = {**record, "_kind": "sol_stake"}
            stake_id = f"{record.get('time')}-{record.get('asset')}-{record.get('amount')}"
            yield RawRecord(self.source_id, f"sol-stake-{stake_id}", _seconds(record.get("time")), payload)
        for record in self._staking_history("/sapi/v1/sol-staking/sol/history/redemptionHistory", since):
            payload = {**record, "_kind": "sol_redeem"}
            redeem_id = f"{record.get('time')}-{record.get('asset')}-{record.get('amount')}"
            yield RawRecord(self.source_id, f"sol-redeem-{redeem_id}", _seconds(record.get("time")), payload)
        for record in self._staking_history("/sapi/v1/sol-staking/sol/history/bnsolRewardsHistory", since):
            payload = {**record, "_kind": "bnsol_reward"}
            reward_id = f"{record.get('time')}-{record.get('amountInSOL')}"
            yield RawRecord(self.source_id, f"bnsol-reward-{reward_id}", _seconds(record.get("time")), payload)

        # Cloud Mining contract charges and refunds are wallet cash flows,
        # outside mining payouts and Spot transactions. Binance documents
        # type 248 as a payment and 249 as a refund.
        for record in self._simple_earn_history(
            "/sapi/v1/asset/ledger-transfer/cloud-mining/queryByPage", since
        ):
            payload = {**record, "_kind": "cloud_mining"}
            yield RawRecord(
                self.source_id,
                f"cloud-mining-{record.get('tranId')}",
                _ms(record.get("createTime")),
                payload,
            )

        # Airdrops / dividends Binance distributes directly to holders.
        for record in self._windowed_history(BASE_URL, "/sapi/v1/asset/assetDividend", since, optional=True):
            payload = {**record, "_kind": "dividend"}
            yield RawRecord(self.source_id, f"dividend-{record.get('tranId')}", _seconds(record.get("divTime")), payload)

        # Futures (USDT-M) income ledger — covers realized P/L, funding
        # fees, commission, and liquidation-adjacent entries in one call.
        futures_symbols = {symbol.upper() for symbol in self.symbols}
        for record in self._windowed_history(FUTURES_BASE_URL, "/fapi/v1/income", since, optional=True):
            payload = {**record, "_kind": "futures_income"}
            if record.get("symbol"):
                futures_symbols.add(str(record["symbol"]).upper())
            yield RawRecord(self.source_id, f"futures-income-{record.get('tranId')}-{record.get('time')}", _ms(record.get("time")), payload)

        # Coin-M futures keeps a distinct income ledger.  It has the same
        # normalized fields as USDT-M but settles P/L and funding in the
        # contract's margin coin, so omitting it made Coin-M balance changes
        # unexplained in Activity.
        for record in self._windowed_history("https://dapi.binance.com", "/dapi/v1/income", since, optional=True):
            payload = {**record, "_kind": "futures_income", "_futures_mode": "coin_m"}
            if record.get("symbol"):
                futures_symbols.add(str(record["symbol"]).upper())
            yield RawRecord(self.source_id, f"coinm-futures-income-{record.get('tranId')}-{record.get('time')}", _ms(record.get("time")), payload)

        # An income row records cash settlement, not the actual contract
        # execution. Query each known active/historical contract's fills as
        # well. These are neutral contract events: treating derivatives as a
        # spot BTC/USDT exchange would corrupt portfolio holdings.
        futures_symbols.update(self._futures_symbols(FUTURES_BASE_URL, "/fapi/v3/positionRisk"))
        futures_symbols.update(self._futures_symbols("https://dapi.binance.com", "/dapi/v1/positionRisk"))
        for symbol in sorted(futures_symbols):
            for mode, base, path in (
                ("usdt_m", FUTURES_BASE_URL, "/fapi/v1/userTrades"),
                ("coin_m", "https://dapi.binance.com", "/dapi/v1/userTrades"),
            ):
                for record in self._futures_trades(base, path, symbol, since):
                    payload = {**record, "_kind": "futures_trade", "_futures_mode": mode}
                    yield RawRecord(
                        self.source_id,
                        f"{mode}-futures-trade-{symbol}-{record.get('id')}",
                        _ms(record.get("time")),
                        payload,
                    )

        # Binance Options is a separate derivatives product with its own
        # balance, fills, exercise ledger and funding-flow endpoint. All are
        # optional: an account without Options enabled must still complete its
        # normal Spot/Futures sync successfully.
        for record in self._windowed_history(OPTIONS_BASE_URL, "/eapi/v1/userTrades", since, optional=True):
            payload = {**record, "_kind": "options_trade"}
            yield RawRecord(self.source_id, f"options-trade-{record.get('id')}", _ms(record.get("time")), payload)
        for record in self._windowed_history(OPTIONS_BASE_URL, "/eapi/v1/exerciseRecord", since, optional=True):
            payload = {**record, "_kind": "options_exercise"}
            yield RawRecord(self.source_id, f"options-exercise-{record.get('id')}", _ms(record.get("createDate")), payload)
        for record in self._windowed_history(
            OPTIONS_BASE_URL,
            "/eapi/v1/bill",
            since,
            {"currency": "USDT"},
            optional=True,
        ):
            payload = {**record, "_kind": "options_bill"}
            yield RawRecord(self.source_id, f"options-bill-{record.get('id')}", _ms(record.get("createDate")), payload)

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
                account_name=self.account_label,
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
                account_name=self.account_label,
                address_to=payload.get("address"),
                notes=f"Binance withdrawal · {payload.get('id')}",
                fees=fees,
                withdrawal_id=payload.get("id"),
                tx_hash=payload.get("txId"),
            )

        if kind in ("trade", "margin_trade"):
            side = "buy" if payload.get("isBuyer") else "sell"
            base_asset, detected_quote_asset = _pair_assets(payload.get("_symbol", ""))
            quote_asset = str(payload.get("quoteAsset") or detected_quote_asset or "").upper() or None
            fees = []
            if payload.get("commission"):
                fees.append(NormalizedFee(fee_type="TRADING_FEE", asset_symbol=str(payload.get("commissionAsset", "")).upper(), amount=str(payload["commission"])))
            return NormalizedEvent(
                event_type="BUY" if side == "buy" else "SELL",
                event_subtype="margin_isolated" if kind == "margin_trade" and payload.get("_is_isolated") else "margin_cross" if kind == "margin_trade" else "spot",
                direction="+" if side == "buy" else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=base_asset,
                amount=str(payload.get("qty", "0")),
                account_name=self.account_label,
                notes=f"Binance {'isolated Margin' if payload.get('_is_isolated') else 'cross Margin' if kind == 'margin_trade' else 'spot'} {side} · {payload['_symbol']}",
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
                account_name=self.account_label,
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
                account_name=self.account_label,
                notes=f"Binance margin repay · {payload.get('txId', '')}",
                order_id=str(payload.get("txId")) if payload.get("txId") is not None else None,
            )

        if kind == "margin_interest":
            return NormalizedEvent(
                event_type="MARGIN_INTEREST",
                event_subtype=f"margin:{payload.get('type', 'interest')}",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("interest", "0")),
                account_name=self.account_label,
                notes=(
                    f"Binance margin interest · {payload.get('type', 'unspecified')}"
                    f" · principal {payload.get('principal', '')}"
                    + (f" · {payload['isolatedSymbol']}" if payload.get("isolatedSymbol") else "")
                ),
                order_id=str(payload.get("txId")) if payload.get("txId") is not None else None,
            )

        if kind == "crypto_loan_borrow":
            status = str(payload.get("status", "")).lower()
            return NormalizedEvent(
                event_type="MARGIN_BORROW",
                event_subtype=f"binance_crypto_loan:{payload.get('loanTerm', 'stable')}",
                direction="+",
                status="COMPLETE" if status not in {"failed", "pending"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("loanCoin", "")).upper(),
                amount=str(payload.get("initialLoanAmount", "0")),
                account_name=self.account_label,
                notes=f"Binance Crypto Loan borrow · {payload.get('orderId', '')}",
                secondary_asset_symbol=str(payload.get("collateralCoin", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("initialCollateralAmount")),
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind == "crypto_loan_repay":
            repay_type = str(payload.get("repayType", ""))
            uses_collateral = repay_type == "2"
            collateral_used = _positive_amount(payload.get("collateralUsed"))
            return NormalizedEvent(
                event_type="LIQUIDATION" if uses_collateral else "MARGIN_REPAY",
                event_subtype="binance_crypto_loan:collateral_repay" if uses_collateral else "binance_crypto_loan",
                direction="-",
                status="COMPLETE" if str(payload.get("repayStatus", "")).lower() == "repaid" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("collateralCoin" if uses_collateral else "loanCoin", "")).upper(),
                amount=collateral_used if uses_collateral and collateral_used else str(payload.get("repayAmount", "0")),
                account_name=self.account_label,
                notes=f"Binance Crypto Loan {'collateral repayment' if uses_collateral else 'repayment'} · {payload.get('orderId', '')}",
                secondary_asset_symbol=(str(payload.get("collateralCoin", "")).upper() or None) if not uses_collateral else None,
                secondary_amount=_positive_amount(payload.get("collateralReturn")) if not uses_collateral else None,
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind == "crypto_loan_adjustment":
            direction = str(payload.get("direction", "")).upper()
            is_additional = direction == "ADDITIONAL"
            return NormalizedEvent(
                event_type="LENDING_DEPOSIT" if is_additional else "LENDING_WITHDRAWAL",
                event_subtype="binance_crypto_loan_collateral_adjustment",
                direction="-" if is_additional else "+",
                status="COMPLETE" if direction in {"ADDITIONAL", "REDUCED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("collateralCoin", "")).upper(),
                amount=str(payload.get("amount", payload.get("collateralAmount", "0"))),
                account_name=self.account_label,
                notes=f"Binance {payload.get('_loan_mode', 'stable')} Crypto Loan collateral {'lock' if is_additional else 'release'} · {payload.get('orderId', '')}",
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind == "crypto_loan_liquidation":
            fee = _positive_amount(payload.get("liquidationFee"))
            fees = [
                NormalizedFee("EXCHANGE_FEE", str(payload.get("collateralCoin", "")).upper(), fee)
            ] if fee else []
            return NormalizedEvent(
                event_type="LIQUIDATION",
                event_subtype="binance_flexible_crypto_loan",
                direction="-",
                status="COMPLETE" if str(payload.get("status", "")).lower() == "liquidated" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("collateralCoin", "")).upper(),
                amount=str(payload.get("liquidationCollateralAmount", "0")),
                account_name=self.account_label,
                notes=f"Binance Flexible Loan liquidation · {payload.get('loanCoin', '')}",
                fees=fees,
                secondary_asset_symbol=str(payload.get("collateralCoin", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("returnCollateralAmount")),
            )

        if kind in ("earn_subscribe", "earn_redeem", "earn_reward"):
            event_type = {"earn_subscribe": "STAKING_DEPOSIT", "earn_redeem": "STAKING_WITHDRAWAL", "earn_reward": "STAKING_REWARD"}[kind]
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"simple_earn_{payload.get('_earn_product', 'flexible')}",
                direction="-" if kind == "earn_subscribe" else "+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("rewards", payload.get("amount", "0"))),
                account_name=self.account_label,
                notes=f"Binance Simple Earn {payload.get('_earn_product', 'flexible')} ({kind.split('_')[1]})",
            )

        if kind in ("yield_subscribe", "yield_redeem", "yield_reward"):
            product = str(payload.get("_yield_product", "yield")).upper()
            if kind == "yield_reward":
                reward_asset = str(payload.get("rewardAsset") or product).upper()
                return NormalizedEvent(
                    event_type="YIELD",
                    event_subtype=f"binance_{product.lower()}_reward",
                    direction="+",
                    status="COMPLETE",
                    occurred_at=occurred_at,
                    original_timestamp=occurred_at.isoformat(),
                    asset_symbol=reward_asset,
                    amount=str(payload.get("rewardsAmount", "0")),
                    account_name=self.account_label,
                    notes=(
                        f"Binance {product} reward · position {payload.get(f'{product}Position', '')}"
                        f" · APR {payload.get('annualPercentageRate', '')}"
                    ),
                )

            is_subscribe = kind == "yield_subscribe"
            status = str(payload.get("status", "")).upper()
            fee_amount = _positive_amount(payload.get("fee"))
            return NormalizedEvent(
                event_type="STAKING_DEPOSIT" if is_subscribe else "STAKING_WITHDRAWAL",
                event_subtype=f"binance_{product.lower()}",
                direction="-",
                status="COMPLETE" if status in {"SUCCESS", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label,
                notes=(
                    f"Binance {product} {'subscription' if is_subscribe else 'redemption'}"
                    + (f" · fee {fee_amount}" if fee_amount else "")
                ),
                secondary_asset_symbol=str(payload.get("receiveAsset", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("receiveAmount")),
            )

        if kind in ("eth_stake", "eth_redeem", "sol_stake", "sol_redeem"):
            is_stake = kind in ("eth_stake", "sol_stake")
            product = "eth" if kind.startswith("eth_") else "sol"
            staked_asset, receipt_asset = ("ETH", "BETH") if product == "eth" else ("SOL", "BNSOL")
            source_asset = str(payload.get("asset") or (staked_asset if is_stake else receipt_asset)).upper()
            destination_asset = str(payload.get("distributeAsset") or (receipt_asset if is_stake else staked_asset)).upper()
            source_amount = str(payload.get("amount", "0"))
            destination_amount = _positive_amount(payload.get("distributeAmount")) or _positive_amount(source_amount)
            return NormalizedEvent(
                event_type="STAKING_DEPOSIT" if is_stake else "STAKING_WITHDRAWAL",
                event_subtype=f"binance_{product}_staking",
                direction="-",
                status="COMPLETE" if str(payload.get("status", "SUCCESS")).upper() in {"SUCCESS", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=source_asset,
                amount=source_amount,
                account_name=self.account_label,
                notes=f"Binance {product.upper()} staking {'subscription' if is_stake else 'redemption'} · {payload.get('purchaseId') or payload.get('redeemId') or payload.get('time') or ''}",
                secondary_asset_symbol=destination_asset,
                secondary_amount=destination_amount,
                order_id=str(payload.get("purchaseId") or payload.get("redeemId")) if payload.get("purchaseId") or payload.get("redeemId") else None,
            )

        if kind in ("wbeth_wrap", "wbeth_unwrap"):
            status = str(payload.get("status", "SUCCESS")).upper()
            return NormalizedEvent(
                event_type="SWAP",
                event_subtype="binance_wbeth_wrap" if kind == "wbeth_wrap" else "binance_wbeth_unwrap",
                direction="-",
                status="COMPLETE" if status in {"SUCCESS", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("fromAsset", "")).upper(),
                amount=str(payload.get("fromAmount", "0")),
                account_name=self.account_label,
                notes=f"Binance WBETH {'wrap' if kind == 'wbeth_wrap' else 'unwrap'} · rate {payload.get('exchangeRate', '')}",
                secondary_asset_symbol=str(payload.get("toAsset", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("toAmount")),
            )

        if kind in ("beth_reward", "wbeth_reward", "bnsol_reward"):
            amount = str(payload.get("amount", payload.get("amountInSOL", "0")))
            reward_asset = str(payload.get("asset") or ("BETH" if kind == "beth_reward" else "WBETH" if kind == "wbeth_reward" else "SOL")).upper()
            return NormalizedEvent(
                event_type="STAKING_REWARD",
                event_subtype=f"binance_{kind}",
                direction="+",
                status="COMPLETE" if str(payload.get("status", "SUCCESS")).upper() in {"SUCCESS", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=reward_asset,
                amount=amount,
                account_name=self.account_label,
                notes=f"Binance {reward_asset} staking reward · APR {payload.get('annualPercentageRate', payload.get('apr', ''))}",
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
                account_name=self.account_label,
                notes=f"Binance convert · {payload.get('fromAsset')} → {payload.get('toAsset')}",
                secondary_asset_symbol=str(payload.get("fromAsset", "")).upper(),
                secondary_amount=_positive_amount(payload.get("fromAmount")),
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
            )

        if kind in ("blvt_subscribe", "blvt_redeem"):
            is_subscribe = kind == "blvt_subscribe"
            fee_amount = _positive_amount(payload.get("fee"))
            fees = [
                NormalizedFee(
                    fee_type="EXCHANGE_FEE",
                    asset_symbol="USDT",
                    amount=fee_amount,
                )
            ] if fee_amount else []
            return NormalizedEvent(
                event_type="BUY" if is_subscribe else "SELL",
                event_subtype="binance_leveraged_token",
                direction="+" if is_subscribe else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("tokenName", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label,
                notes=f"Binance Leveraged Token {'subscription' if is_subscribe else 'redemption'} · NAV {payload.get('nav', '')}",
                fees=fees,
                secondary_asset_symbol="USDT",
                secondary_amount=_positive_amount(payload.get("totalCharge" if is_subscribe else "netProceed")),
                order_id=str(payload.get("id")) if payload.get("id") is not None else None,
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
                account_name=self.account_label,
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
                account_name=self.account_label,
                notes=f"Binance sell crypto ({payload.get('paymentMethod', 'fiat')}) · {payload.get('orderNo', '')}",
                fees=_fiat_fees(payload),
                secondary_asset_symbol=str(payload.get("fiatCurrency", "")).upper(),
                secondary_amount=_positive_amount(payload.get("obtainAmount")),
                order_id=payload.get("orderNo"),
            )

        if kind in ("fiat_deposit", "fiat_withdrawal"):
            is_deposit = kind == "fiat_deposit"
            fee_amount = _positive_amount(payload.get("totalFee"))
            currency = str(payload.get("fiatCurrency", "")).upper()
            fees = [NormalizedFee("EXCHANGE_FEE", currency, fee_amount)] if fee_amount else []
            status = str(payload.get("status", "")).upper()
            return NormalizedEvent(
                event_type="DEPOSIT" if is_deposit else "WITHDRAWAL",
                event_subtype="fiat",
                direction="+" if is_deposit else "-",
                status="COMPLETE" if status in {"SUCCESSFUL", "FINISHED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=currency,
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label,
                notes=f"Binance fiat {'deposit' if is_deposit else 'withdrawal'} · {payload.get('method', '')} · {payload.get('orderNo', '')}",
                fees=fees,
                order_id=str(payload.get("orderNo")) if payload.get("orderNo") else None,
            )

        if kind == "pay":
            order_type = str(payload.get("orderType", "")).upper()
            is_payment = order_type == "PAY"
            is_refund = order_type == "PAY_REFUND"
            wallet_identity = payload.get("receiverInfo") if is_payment else payload.get("payerInfo") if is_refund else None
            if isinstance(wallet_identity, dict):
                wallet_identity = wallet_identity.get("name") or wallet_identity.get("email") or wallet_identity.get("userId")
            return NormalizedEvent(
                event_type="PAYMENT" if is_payment else "RECEIVE" if is_refund else "UNKNOWN",
                event_subtype=f"binance_pay:{order_type or 'unspecified'}",
                direction="-" if is_payment else "+",
                status="COMPLETE" if (is_payment or is_refund) else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("currency", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label if is_payment else str(wallet_identity) if wallet_identity else self.account_label,
                address_to=str(wallet_identity) if is_payment else self.account_label if is_refund else None,
                notes=(
                    f"Binance Pay {order_type or 'transaction'} · {payload.get('transactionId', '')}"
                    if (is_payment or is_refund)
                    else f"Binance Pay {order_type or 'transaction'} · direction unavailable in API; review required"
                ),
                order_id=str(payload.get("transactionId")) if payload.get("transactionId") else None,
            )

        if kind == "spot_rebate":
            rebate_type = str(payload.get("type", ""))
            event_type = {"1": "CASHBACK", "2": "REFERRAL_REWARD"}.get(rebate_type, "UNKNOWN")
            label = {"1": "commission rebate", "2": "referral kickback"}.get(rebate_type, "unrecognized rebate")
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"binance_spot_rebate:{rebate_type or 'unspecified'}",
                direction="+",
                status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label,
                notes=f"Binance Spot {label} · {payload.get('updateTime', '')}",
            )

        if kind == "cloud_mining":
            record_type = str(payload.get("type", ""))
            is_payment = record_type == "248"
            is_refund = record_type == "249"
            return NormalizedEvent(
                event_type="PAYMENT" if is_payment else "RECEIVE" if is_refund else "UNKNOWN",
                event_subtype=f"binance_cloud_mining:{record_type or 'unspecified'}",
                direction="-" if is_payment else "+",
                status="COMPLETE" if (is_payment or is_refund) else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")).lstrip("-"),
                account_name=self.account_label,
                notes=(
                    "Binance Cloud Mining payment"
                    if is_payment
                    else "Binance Cloud Mining refund"
                    if is_refund
                    else f"Binance Cloud Mining unrecognized record type {record_type or 'unspecified'}"
                ),
                order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            )

        if kind == "universal_transfer":
            transfer_type = str(payload.get("type", "")).upper()
            status = str(payload.get("status", "")).upper()
            asset = str(payload.get("asset", "")).upper()
            amount = str(payload.get("amount", "0"))
            return NormalizedEvent(
                event_type="TRANSFER",
                event_subtype=f"binance_internal:{transfer_type or 'unspecified'}",
                direction="-",
                status="COMPLETE" if status in {"CONFIRMED", "SUCCESS", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=asset,
                amount=amount,
                account_name=self.account_label,
                notes=f"Binance internal transfer · {(transfer_type or 'unspecified').replace('_', ' → ')}",
                secondary_asset_symbol=asset or None,
                secondary_amount=_positive_amount(amount),
                order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            )

        if kind == "mining_account_adjustment":
            adjustment_type = str(payload.get("type", ""))
            event_type, label = {
                "0": ("REFERRAL_REWARD", "referral"),
                "1": ("RECEIVE", "refund"),
                "2": ("CASHBACK", "rebate"),
            }.get(adjustment_type, ("UNKNOWN", "unrecognized adjustment"))
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"binance_mining_account:{adjustment_type or 'unspecified'}",
                direction="+",
                status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coinName", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label,
                notes=f"Binance Pool {label} · {payload.get('subName', '')} · {payload.get('_mining_algo', '')}",
            )

        if kind in ("mining_earning", "mining_bonus"):
            payout_type = str(payload.get("type", ""))
            is_bonus = kind == "mining_bonus"
            event_type = "MINING_REWARD"
            if is_bonus and payout_type == "3":
                event_type = "CASHBACK"
            elif is_bonus and payout_type == "6":
                event_type = "INCOME"
            elif not is_bonus and payout_type == "8":
                event_type = "UNKNOWN"
            status = str(payload.get("status", ""))
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"binance_mining:{'bonus' if is_bonus else 'earning'}:{payout_type or 'unspecified'}",
                direction="+",
                status="COMPLETE" if status == "2" and event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coinName", "")).upper(),
                amount=str(payload.get("profitAmount", "0")),
                account_name=self.account_label,
                notes=(
                    f"Binance Pool {'bonus' if is_bonus else 'earning'} · {payload.get('_mining_account', '')} · "
                    f"{payload.get('_mining_algo', '')} · type {payout_type or 'unspecified'} · "
                    f"daily hashrate {payload.get('dayHashRate', '')}"
                ),
            )

        if kind == "c2c":
            side = str(payload.get("tradeType", "")).upper()
            fees = []
            commission = _positive_amount(payload.get("commission"))
            if commission:
                fees.append(
                    NormalizedFee(
                        fee_type="EXCHANGE_FEE",
                        asset_symbol=str(payload.get("asset", "")).upper(),
                        amount=commission,
                    )
                )
            return NormalizedEvent(
                event_type="BUY" if side == "BUY" else "SELL",
                event_subtype="c2c",
                direction="+" if side == "BUY" else "-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset", "")).upper(),
                amount=str(payload.get("amount", "0")),
                account_name=self.account_label if side == "SELL" else str(payload.get("counterPartNickName") or self.account_label),
                address_to=str(payload.get("counterPartNickName") or self.account_label) if side == "SELL" else self.account_label,
                notes=f"Binance C2C {side.lower()} · {payload.get('advertisementRole', 'order')} · {payload.get('orderNumber', '')}",
                fees=fees,
                secondary_asset_symbol=str(payload.get("fiat", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("totalPrice")),
                order_id=payload.get("orderNumber"),
            )

        if kind == "auto_invest":
            status = str(payload.get("transactionStatus", "")).upper()
            fee_amount = _positive_amount(payload.get("transactionFee"))
            fees = [
                NormalizedFee(
                    fee_type="TRADING_FEE",
                    asset_symbol=str(payload.get("transactionFeeUnit", "")).upper(),
                    amount=fee_amount,
                )
            ] if fee_amount and payload.get("transactionFeeUnit") else []
            return NormalizedEvent(
                event_type="BUY",
                event_subtype="auto_invest",
                direction="+",
                status="COMPLETE" if status in {"SUCCESS", "SUCCESSFUL", "COMPLETED"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("targetAsset", "")).upper(),
                amount=str(payload.get("targetAssetAmount", "0")),
                account_name=self.account_label,
                notes=f"Binance Auto-Invest · {payload.get('planName', payload.get('planId', ''))}",
                fees=fees,
                secondary_asset_symbol=str(payload.get("sourceAsset", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("sourceAssetAmount")),
                order_id=str(payload.get("id") or payload.get("transactionId")) if payload.get("id") or payload.get("transactionId") else None,
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
                account_name=self.account_label,
                notes=f"Binance asset dividend · {payload.get('enInfo', '')}",
                order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            )

        if kind == "futures_trade":
            side = str(payload.get("side", "")).upper()
            position_side = str(payload.get("positionSide", "")).upper()
            is_open = (position_side == "LONG" and side == "BUY") or (position_side == "SHORT" and side == "SELL")
            is_close = (position_side == "LONG" and side == "SELL") or (position_side == "SHORT" and side == "BUY")
            margin_asset = str(payload.get("marginAsset") or payload.get("commissionAsset") or "USDT").upper()
            notional = _positive_amount(payload.get("quoteQty")) or _positive_amount(payload.get("baseQty")) or _positive_amount(payload.get("qty")) or "0"
            fee_note = _positive_amount(payload.get("commission"))
            return NormalizedEvent(
                event_type="FUTURES_OPEN" if is_open else "FUTURES_CLOSE" if is_close else "UNKNOWN",
                event_subtype=f"binance_futures:{payload.get('_futures_mode', 'usdt_m')}:{position_side or 'unspecified'}",
                direction="-",
                status="COMPLETE" if (is_open or is_close) else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=margin_asset,
                amount=notional,
                account_name=self.account_label,
                notes=(
                    f"Binance {payload.get('_futures_mode', 'usdt_m')} futures {side or 'fill'} · "
                    f"{payload.get('symbol', '')} · {position_side or 'position unspecified'} · "
                    f"price {payload.get('price', '')} · realized P/L {payload.get('realizedPnl', '')}"
                    + (f" · commission {fee_note} {payload.get('commissionAsset', '')}" if fee_note else "")
                ),
                secondary_asset_symbol=margin_asset,
                secondary_amount=notional,
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
                trade_id=str(payload.get("id")) if payload.get("id") is not None else None,
            )

        if kind == "options_trade":
            quote_asset = str(payload.get("quoteAsset") or "USDT").upper()
            notional = _product_amount(payload.get("price"), payload.get("quantity")) or _positive_amount(payload.get("quantity")) or "0"
            return NormalizedEvent(
                event_type="OPTION_TRADE",
                event_subtype=f"binance_options:{payload.get('optionSide', 'unspecified')}",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=quote_asset,
                amount=notional,
                account_name=self.account_label,
                notes=(
                    f"Binance Options {payload.get('side', 'trade')} · {payload.get('symbol', '')} · "
                    f"{payload.get('optionSide', 'option')} · price {payload.get('price', '')} · "
                    f"realized P/L {payload.get('realizedProfit', '')} · fee {payload.get('fee', '')}"
                ),
                secondary_asset_symbol=quote_asset,
                secondary_amount=notional,
                order_id=str(payload.get("orderId")) if payload.get("orderId") is not None else None,
                trade_id=str(payload.get("tradeId") or payload.get("id")) if payload.get("tradeId") is not None or payload.get("id") is not None else None,
            )

        if kind == "options_exercise":
            amount_raw = str(payload.get("amount", "0"))
            fee_amount = _positive_amount(payload.get("fee"))
            currency = str(payload.get("currency") or payload.get("quoteAsset") or "USDT").upper()
            fees = [NormalizedFee("EXCHANGE_FEE", currency, fee_amount)] if fee_amount else []
            return NormalizedEvent(
                event_type="OPTION_EXERCISE",
                event_subtype=f"binance_options:{payload.get('optionSide', 'unspecified')}",
                direction="-" if amount_raw.startswith("-") else "+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=currency,
                amount=amount_raw.lstrip("-") or "0",
                account_name=self.account_label,
                notes=(
                    f"Binance Options exercise · {payload.get('symbol', '')} · "
                    f"strike {payload.get('exercisePrice', '')} · {payload.get('positionSide', '')}"
                ),
                fees=fees,
                order_id=str(payload.get("id")) if payload.get("id") is not None else None,
            )

        if kind == "options_bill":
            amount_raw = str(payload.get("amount", "0"))
            bill_type = str(payload.get("type", "")).upper()
            event_type = "EXCHANGE_FEE" if "FEE" in bill_type else "UNKNOWN"
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"binance_options:{bill_type or 'funding_flow'}",
                direction="-" if amount_raw.startswith("-") else "+",
                status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("asset") or "USDT").upper(),
                amount=amount_raw.lstrip("-") or "0",
                account_name=self.account_label,
                notes=f"Binance Options funding flow · {bill_type or 'unrecognized'} · {payload.get('id', '')}",
                order_id=str(payload.get("id")) if payload.get("id") is not None else None,
            )

        # futures_income
        income_type = str(payload.get("incomeType", ""))
        event_type = _INCOME_TYPE_MAP.get(income_type, "UNKNOWN")
        amount_raw = str(payload.get("income", "0"))
        return NormalizedEvent(
            event_type=event_type,
            event_subtype=f"futures:{payload.get('_futures_mode', 'usdt_m')}:{income_type or 'unspecified'}",
            direction="-" if amount_raw.startswith("-") else "+",
            status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol=str(payload.get("asset", "")).upper(),
            amount=amount_raw.lstrip("-") or "0",
            account_name=self.account_label,
            notes=f"Binance {payload.get('_futures_mode', 'usdt_m')} futures {income_type or 'unrecognized income type'} · {payload.get('symbol', '')}",
            order_id=str(payload.get("tranId")) if payload.get("tranId") is not None else None,
            trade_id=str(payload.get("tradeId")) if payload.get("tradeId") else None,
        )


def _ms(value) -> datetime | None:
    """Parse Binance timestamps across the exchange's inconsistent formats.

    Most endpoints return Unix milliseconds, while some older/product-specific
    history endpoints return Unix seconds or a UTC date string such as
    ``2024-10-06 08:29:51``.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    text = str(value).strip()
    try:
        numeric = Decimal(text)
        # Unix seconds are currently ~1e9; milliseconds are ~1e12. Keep the
        # threshold generous so decimal-formatted provider values work too.
        timestamp_seconds = numeric if abs(numeric) < Decimal("10000000000") else numeric / Decimal(1000)
        return datetime.fromtimestamp(float(timestamp_seconds), tz=timezone.utc)
    except (InvalidOperation, OverflowError, ValueError, OSError):
        pass

    # Binance has returned both a plain UTC date and ISO-8601 timestamps from
    # different product ledgers. Treat a timezone-less provider date as UTC.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"Unsupported Binance timestamp: {value!r}") from exc
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def _product_amount(price, quantity) -> str | None:
    try:
        amount = Decimal(str(price)) * Decimal(str(quantity))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount == 0:
        return None
    return format(abs(amount), "f")


def _fiat_fees(payload: dict) -> list[NormalizedFee]:
    amount = _positive_amount(payload.get("totalFee"))
    if not amount:
        return []
    return [NormalizedFee(fee_type="EXCHANGE_FEE", asset_symbol=str(payload.get("fiatCurrency", "")).upper(), amount=amount)]
