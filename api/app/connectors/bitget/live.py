from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urlencode

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

# Classic endpoints expose different maximum windows, but all of the private
# history APIs below retain at most 90 days.  Keep the generic window at 90
# days and use a smaller 30-day window for futures account bills, whose API
# explicitly enforces it.
_CLASSIC_LOOKBACK_MS = 90 * 24 * 3600 * 1000
_CLASSIC_WINDOW_MS = _CLASSIC_LOOKBACK_MS
_CLASSIC_FUTURES_WINDOW_MS = 30 * 24 * 3600 * 1000

# financial-records is a broad, noisy ledger — it includes one entry per
# order fill (ORDER_DEALT_IN/OUT, OPEN_LONG, CLOSE_SHORT, ...) which would
# duplicate what /api/v3/trade/fills already yields as BUY/SELL. Only the
# types below (interest, funding fees, liquidation, margin borrow/repay)
# aren't already covered by fills, so only these are mapped; everything
# else is skipped rather than guessed (avoids double-counting).
#
# TRANSFER_IN/TRANSFER_OUT are internal to a Unified Account, not external
# deposits or withdrawals. We still retain them as visible, raw-backed,
# *balance-neutral* Activity events: the same asset/amount is used on both
# legs, so the mirrored IN/OUT records cannot double-count holdings.
_UTA_FINANCIAL_TYPE_MAP = {
    "INTEREST_SETTLEMENT_OUT": ("MARGIN_INTEREST", "-"),
    "CONTRACT_MAIN_SETTLE_FEE_USER_IN": ("FUNDING_PAYMENT", "+"),
    "CONTRACT_MAIN_SETTLE_FEE_USER_OUT": ("FUNDING_PAYMENT", "-"),
    "MARGIN_SETTLE_FEE_USER_IN": ("FUNDING_PAYMENT", "+"),
    "MARGIN_SETTLE_FEE_USER_OUT": ("FUNDING_PAYMENT", "-"),
    "RWA_FIXED_SETTLE_FEE_USER_IN": ("FUNDING_PAYMENT", "+"),
    "RWA_FIXED_SETTLE_FEE_USER_OUT": ("FUNDING_PAYMENT", "-"),
    "RWA_CONTRACT_MAIN_SETTLE_FEE_USER_IN": ("FUNDING_PAYMENT", "+"),
    "RWA_CONTRACT_MAIN_SETTLE_FEE_USER_OUT": ("FUNDING_PAYMENT", "-"),
    "BURST_CLOSE_LONG": ("LIQUIDATION", "-"),
    "BURST_CLOSE_SHORT": ("LIQUIDATION", "-"),
    "BURST_BUY_SSM": ("LIQUIDATION", "-"),
    "BURST_SELL_SSM": ("LIQUIDATION", "-"),
    "RISK_LIQ_USER_IN": ("LIQUIDATION", "+"),
    "RISK_LIQ_USER_OUT": ("LIQUIDATION", "-"),
    "FIXED_RISK_LIQ_USER_IN": ("LIQUIDATION", "+"),
    "FIXED_RISK_LIQ_USER_OUT": ("LIQUIDATION", "-"),
    "RISK_LIQ_DEFAULT_USER_IN": ("LIQUIDATION", "+"),
    "FIXED_RISK_LIQ_DEFAULT_USER_IN": ("LIQUIDATION", "+"),
    "LIQ_FEE": ("LIQUIDATION", "-"),
    "LIQ_REPAYMENT": ("LIQUIDATION", "-"),
    "BORROW": ("MARGIN_BORROW", "+"),
    "REPAYMENT": ("MARGIN_REPAY", "-"),
    "INTEREST_REPAYMENT": ("MARGIN_INTEREST", "-"),
    "TRACE_SHARE_BENEFIT_USER_OUT": ("FEE", "-"),
    "TRACE_SHARE_BENEFIT_USER_IN": ("INCOME", "+"),
    "BONUS_GRANT_USER_IN": ("CASHBACK", "+"),
    "BONUS_EXPIRE_USER_OUT": ("FEE", "-"),
    "BONUS_TRANSFER_USER_OUT": ("FEE", "-"),
    "DELIST_MARGIN_TOKEN_SOURCE_USER_OUT": ("SELL", "-"),
    "DELIST_MARGIN_TOKEN_TARGET_USER_IN": ("BUY", "+"),
    "DELIST_SMALL_BALANCE_USER_OUT": ("SELL", "-"),
    "DELIST_SMALL_LIABILITY_USER_IN": ("BUY", "+"),
    "SMALL_ASSET_SOURCE_TOKEN_USER_OUT": ("SELL", "-"),
    "SMALL_ASSET_TARGET_TOKEN_USER_IN": ("BUY", "+"),
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
        return "bitget-live-0.13"

    @property
    def history_limit_note(self) -> str | None:
        """Surfaced by sync_account() after a backfill — real, unavoidable
        API limits (not something more pagination fixes), so the user should
        know why history stops there rather than assume the sync is broken."""
        if self.mode == "uta":
            return (
                "Bitget's Unified Account API only exposes the last 90 days of history — anything older isn't "
                "retrievable through this connection. Copy-trading and bot trades also won't appear if Bitget "
                "runs them in an isolated sub-account rather than your main account — Bitget's own docs say API "
                "access isn't available for bot-trading sub-accounts, so that portion can't be pulled automatically. "
                "Elite Earn balances are included, but its history response has no per-record timestamp; it cannot "
                "be safely turned into date-valued Activity without inventing a transaction time."
            )
        if self.mode == "classic":
            return (
                "Bitget's Classic private-history APIs retain at most 90 days for the account records used by this "
                "connection. This sync retrieves every available page in that window; export and import older Bitget "
                "history from the exchange if you need a complete earlier ledger."
            )
        return None

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        params = params or {}
        # The exact encoded query string is part of Bitget's signature.  It
        # must also be URL-safe for cursor values and account identifiers.
        query = urlencode(params)
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

    def _get_optional(self, path: str, params: dict | None = None) -> dict | list:
        """Like _get, but a failure (missing permission, endpoint drift on a
        less-exercised product) just means this category contributes
        nothing this round instead of aborting the whole sync."""
        try:
            return self._get(path, params)
        except ConnectorUnavailable:
            return []

    @staticmethod
    def _records(data: dict | list, *keys: str) -> list[dict]:
        """Extract a record list from the several response shapes Bitget
        uses across Classic and UTA product APIs."""
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _v2_paged(
        self,
        path: str,
        params: dict,
        *,
        record_keys: tuple[str, ...] = (),
        id_key: str,
        optional: bool = False,
    ) -> Iterable[dict]:
        """Read every Classic API page rather than silently accepting the
        first 100 records.  APIs that return an envelope provide ``endId``;
        the spot fills API returns a plain list, where its last record's
        stable ID is the documented ``idLessThan`` cursor."""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page_params = dict(params)
            if cursor:
                page_params["idLessThan"] = cursor
            try:
                data = self._get(path, page_params)
            except ConnectorUnavailable:
                if optional:
                    return
                raise
            records = self._records(data, *record_keys)
            yield from records
            next_cursor = data.get("endId") if isinstance(data, dict) else None
            if next_cursor is None and records:
                next_cursor = records[-1].get(id_key)
            next_cursor = str(next_cursor) if next_cursor not in (None, "") else None
            if not records or not next_cursor or next_cursor in seen_cursors:
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _v2_windowed(
        self,
        path: str,
        params: dict,
        start_ms: int,
        end_ms: int,
        *,
        window_ms: int = _CLASSIC_WINDOW_MS,
        record_keys: tuple[str, ...] = (),
        id_key: str,
        optional: bool = False,
    ) -> Iterable[dict]:
        """Page a Classic endpoint across its documented time windows."""
        window_start = start_ms
        while window_start < end_ms:
            window_end = min(window_start + window_ms, end_ms)
            page_params = {
                **params,
                "startTime": str(window_start),
                "endTime": str(window_end),
                "limit": "100",
            }
            try:
                yield from self._v2_paged(
                    path, page_params, record_keys=record_keys, id_key=id_key, optional=optional
                )
            except ConnectorUnavailable:
                if optional:
                    return
                raise
            window_start = window_end

    def _v2_numbered_windowed(
        self, path: str, params: dict, start_ms: int, end_ms: int, *, optional: bool = False
    ) -> Iterable[dict]:
        """Page Classic endpoints using ``pageNo``/``pageSize`` rather
        than their common ``idLessThan`` cursor (Classic Crypto Loans)."""
        window_start = start_ms
        while window_start < end_ms:
            window_end = min(window_start + _CLASSIC_WINDOW_MS, end_ms)
            page_no = 1
            while True:
                request = {
                    **params,
                    "startTime": str(window_start),
                    "endTime": str(window_end),
                    "pageNo": str(page_no),
                    "pageSize": "100",
                }
                try:
                    records = self._records(self._get(path, request), "list", "resultList")
                except ConnectorUnavailable:
                    if optional:
                        break
                    raise
                yield from records
                if len(records) < 100:
                    break
                page_no += 1
            window_start = window_end

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

    def _v3_numbered_windowed(
        self, path: str, params: dict, start_ms: int, end_ms: int, *, optional: bool = False
    ) -> Iterable[dict]:
        """Page the V3 endpoints which use ``pageNum``/``pageSize`` rather
        than a cursor (notably Crypto Loans).  Their response is a bare
        list, so a short page is the only documented end marker."""
        window_start = start_ms
        while window_start < end_ms:
            window_end = min(window_start + _UTA_WINDOW_MS, end_ms)
            page_num = 1
            while True:
                request = {
                    **params,
                    "startTime": str(window_start),
                    "endTime": str(window_end),
                    "pageNum": str(page_num),
                    "pageSize": "100",
                }
                try:
                    records = self._records(self._get(path, request), "list", "resultList")
                except ConnectorUnavailable:
                    if optional:
                        break
                    raise
                yield from records
                if len(records) < 100:
                    break
                page_num += 1
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

        # Futures fills — position opens/closes, including copy-trading and
        # bot-generated trades that run on your *main* account (Bitget's
        # fills API doesn't distinguish how an order originated, only that
        # it filled). This was previously never fetched at all: only a
        # narrow set of financial-records types (funding fees, liquidation)
        # was mapped, so ordinary futures trading — and therefore any
        # copy-trading or bot activity riding on it — was invisible.
        # Copy-trading/bot funds that Bitget isolates into their own
        # sub-account are a separate limitation — see history_limit_note.
        for category in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            for fill in self._v3_windowed("/api/v3/trade/fills", {"category": category}, start_ms, end_ms, optional=True):
                payload = {**fill, "_kind": "uta_futures_fill", "_category": category}
                yield RawRecord(self.source_id, f"uta-futures-fill-{fill.get('execId')}", _ms(fill.get("createdTime")), payload)

        for record in self._v3_windowed("/api/v3/account/deposit-records", {}, start_ms, end_ms):
            payload = {**record, "_kind": "uta_deposit"}
            record_id = record.get("recordId") or record.get("orderId")
            yield RawRecord(self.source_id, f"uta-deposit-{record_id}", _ms(record.get("createdTime")), payload)

        for record in self._v3_windowed("/api/v3/account/withdrawal-records", {}, start_ms, end_ms):
            payload = {**record, "_kind": "uta_withdrawal"}
            record_id = record.get("recordId") or record.get("orderId")
            yield RawRecord(self.source_id, f"uta-withdrawal-{record_id}", _ms(record.get("createdTime")), payload)

        # The API documents OTHER as a valid catch-all category. Its unknown
        # type rows remain review-required rather than fabricated, but raw
        # evidence must be imported so newer Bitget products never vanish.
        for category in ("SPOT", "MARGIN", "USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES", "OTHER"):
            for record in self._v3_windowed("/api/v3/account/financial-records", {"category": category}, start_ms, end_ms, optional=True):
                if _is_uta_internal_transfer(record):
                    payload = {**record, "_kind": "uta_internal_transfer", "_category": category}
                    yield RawRecord(self.source_id, f"uta-internal-transfer-{category}-{record.get('id')}", _ms(record.get("ts")), payload)
                    continue
                if not _is_additional_uta_financial(record):
                    continue
                payload = {**record, "_kind": "uta_financial", "_category": category}
                yield RawRecord(self.source_id, f"uta-financial-{record.get('id')}", _ms(record.get("ts")), payload)

        for record in self._v3_windowed("/api/v3/account/convert-records", {}, start_ms, end_ms, optional=True):
            payload = {**record, "_kind": "uta_convert"}
            key = f"{record.get('ts')}-{record.get('fromCoin')}-{record.get('toCoin')}"
            yield RawRecord(self.source_id, f"uta-convert-{key}", _ms(record.get("ts")), payload)

        # A lead account is a distinct copy-trading allocation inside
        # Bitget.  Its transfer history is permission-gated and optional, but
        # when available it is the canonical evidence for funds moving into
        # or out of the lead portfolio (not an external wallet movement).
        for record in self._v3_windowed("/api/v3/copy/futures/transfer-record", {}, start_ms, end_ms, optional=True):
            payload = {**record, "_kind": "uta_copy_transfer"}
            yield RawRecord(self.source_id, f"uta-copy-transfer-{record.get('transferId')}", _ms(record.get("createdTime")), payload)

        # Crypto Loans are distinct from margin borrowing: the history
        # carries pledged collateral, repayment principal/interest and forced
        # reductions.  Import those independently so a loan cannot look like
        # a normal spot withdrawal or silently omit its interest expense.
        for record in self._v3_numbered_windowed("/api/v3/loan/borrow-history", {}, start_ms, end_ms, optional=True):
            order_id = record.get("orderId")
            payload = {**record, "_kind": "uta_loan_borrow"}
            yield RawRecord(self.source_id, f"uta-loan-borrow-{order_id}", _ms(record.get("borrowTime")), payload)
        for record in self._v3_numbered_windowed("/api/v3/loan/repay-history", {}, start_ms, end_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('repayTime')}"
            parts: list[str] = []
            if _positive_amount(record.get("repayLoanAmount")):
                parts.append("principal")
            if _positive_amount(record.get("payInterest")):
                parts.append("interest")
            # Usually an unlocked pledge is the second leg of the principal
            # repayment.  Keep a standalone record only for a pure release.
            if _positive_amount(record.get("repayUnlockAmount")) and "principal" not in parts:
                parts.append("collateral")
            for part in parts:
                payload = {**record, "_kind": f"uta_loan_repay_{part}"}
                yield RawRecord(self.source_id, f"uta-loan-repay-{record_id}-{part}", _ms(record.get("repayTime")), payload)
        for record in self._v3_numbered_windowed("/api/v3/loan/reduces", {}, start_ms, end_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('reduceTime')}"
            payload = {**record, "_kind": "uta_loan_reduce"}
            yield RawRecord(self.source_id, f"uta-loan-reduce-{record_id}", _ms(record.get("reduceTime")), payload)
        for record in self._v3_numbered_windowed("/api/v3/loan/pledge-rate-history", {}, start_ms, end_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('reviseTime')}"
            payload = {**record, "_kind": "uta_loan_pledge_adjustment"}
            yield RawRecord(self.source_id, f"uta-loan-pledge-{record_id}", _ms(record.get("reviseTime")), payload)

    def fetch_balances(self) -> Iterable[Balance]:
        if self.mode is None:
            self._detect_mode()
        if self.mode == "uta":
            # UTA keeps the trading account and funding account separate.
            # Leaving out funding assets was a direct reason a freshly
            # connected account could appear to be missing coins even though
            # the API key and sync were healthy.
            trading = self._records(self._get("/api/v3/account/assets"), "assets", "list")
            funding = self._records(self._get_optional("/api/v3/account/funding-assets"), "assets", "list")
            # On-chain Elite Earn positions are not reported in either the
            # UTA trading or funding wallets.  Include their product-coin
            # holdings in reconciliation without fabricating historical
            # Activity: the public record schema does not provide event
            # timestamps, so it cannot be safely valued or tax-dated.
            elite = self._records(self._get_optional("/api/v3/earn/elite-assets"), "resultList", "list")
            elite_balances = [
                {"coin": row.get("productCoin"), "balance": row.get("holdingAmount")}
                for row in elite
            ]
            return _aggregate_balances((*trading, *funding, *elite_balances))
        # Classic accounts segregate Spot and each futures product.  Using
        # only the Spot endpoint made valid USDT-/USDC-/Coin-M collateral
        # look absent after a successful sync, even though this connector
        # imports those futures executions.  Keep Spot mandatory but merge
        # all documented product balance lists that the API key can read.
        spot = self._records(self._get("/api/v2/spot/account/assets"), "assets", "list")
        futures: list[dict] = []
        for product in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            data = self._get_optional("/api/v2/mix/account/accounts", {"productType": product})
            futures.extend(self._records(data, "assets", "list"))
        # Margin asset rows expose `net` (available + frozen - borrow -
        # interest), which is the balance a ledger reconciliation must use.
        # Both endpoints return all symbols/assets when their optional filter
        # is omitted, so this also discovers isolated accounts without a
        # guessed pair list.
        crossed = self._records(self._get_optional("/api/v2/margin/crossed/account/assets"), "assets", "list")
        isolated = self._records(self._get_optional("/api/v2/margin/isolated/account/assets"), "assets", "list")
        # Crypto Loan collateral is held outside the ordinary Spot wallet;
        # include it in the snapshot so a fully-collateralised position does
        # not look like missing coins during reconciliation. Debt is a
        # liability, therefore deliberately isn't added as an asset balance.
        loan_debts = self._get_optional("/api/v2/earn/loan/debts")
        pledge_infos = loan_debts.get("pledgeInfos", []) if isinstance(loan_debts, dict) else []
        pledged = [
            {"coin": row.get("coin"), "balance": row.get("amount")}
            for row in pledge_infos
            if isinstance(row, dict)
        ]
        return _aggregate_balances((*spot, *futures, *crossed, *isolated, *pledged))

    def _fetch_classic(self, since: datetime | None = None) -> Iterable[RawRecord]:
        now_ms = int(time.time() * 1000)
        start_ms = int(since.timestamp() * 1000) if since is not None else now_ms - _CLASSIC_LOOKBACK_MS
        start_ms = max(start_ms, now_ms - _CLASSIC_LOOKBACK_MS)

        # Spot trade, deposit and withdrawal endpoints are core evidence:
        # permissions/network errors here must fail visibly rather than make
        # a successful-looking sync with an incomplete history.
        for fill in self._v2_windowed(
            "/api/v2/spot/trade/fills", {}, start_ms, now_ms, id_key="tradeId"
        ):
            payload = {**fill, "_kind": "fill"}
            yield RawRecord(self.source_id, f"fill-{fill['tradeId']}", _ms(fill.get("cTime")), payload)

        for record in self._v2_windowed(
            "/api/v2/spot/wallet/deposit-records", {}, start_ms, now_ms, id_key="orderId"
        ):
            payload = {**record, "_kind": "deposit"}
            yield RawRecord(self.source_id, f"deposit-{record['orderId']}", _ms(record.get("cTime")), payload)

        for record in self._v2_windowed(
            "/api/v2/spot/wallet/withdrawal-records", {}, start_ms, now_ms, id_key="orderId"
        ):
            payload = {**record, "_kind": "withdrawal"}
            yield RawRecord(self.source_id, f"withdrawal-{record['orderId']}", _ms(record.get("cTime")), payload)

        # Cross-margin borrow/repay history.
        for record in self._v2_windowed(
            "/api/v2/margin/crossed/borrow-history", {}, start_ms, now_ms, id_key="loanId", optional=True
        ):
            payload = {**record, "_kind": "margin_borrow", "_margin_mode": "cross"}
            yield RawRecord(self.source_id, f"margin-borrow-{record.get('loanId', record.get('cTime'))}", _ms(record.get("cTime")), payload)
        for record in self._v2_windowed(
            "/api/v2/margin/crossed/repay-history", {}, start_ms, now_ms, id_key="repayId", optional=True
        ):
            payload = {**record, "_kind": "margin_repay", "_margin_mode": "cross"}
            yield RawRecord(self.source_id, f"margin-repay-{record.get('repayId', record.get('cTime'))}", _ms(record.get("cTime")), payload)

        # Margin has a financial ledger separate from the loan/repay
        # endpoints.  Its deal, liquidation, compensation and system-exchange
        # rows are the only evidence for a considerable part of Classic
        # cross/isolated Margin activity.  We omit borrow/repay (already
        # represented above) and account-to-account transfers (internal
        # reshuffles within this one linked exchange account) to avoid
        # double-counting holdings.
        for record in self._v2_windowed(
            "/api/v2/margin/crossed/financial-records",
            {},
            start_ms,
            now_ms,
            record_keys=("resultList",),
            id_key="marginId",
            optional=True,
        ):
            if _is_margin_internal_transfer(record):
                payload = {**record, "_kind": "margin_internal_transfer", "_margin_mode": "crossed"}
                yield RawRecord(self.source_id, f"margin-internal-transfer-crossed-{record.get('marginId')}", _ms(record.get("cTime")), payload)
            elif _is_additional_margin_financial(record):
                payload = {**record, "_kind": "margin_financial", "_margin_mode": "crossed"}
                yield RawRecord(self.source_id, f"margin-financial-crossed-{record.get('marginId')}", _ms(record.get("cTime")), payload)

        # The isolated ledger requires a symbol.  Its assets endpoint is a
        # read-only discovery source and accepts no symbol to list all pairs
        # that have an isolated balance/history, avoiding a guessed global
        # symbol universe and its rate-limit cost.
        isolated_assets = self._records(self._get_optional("/api/v2/margin/isolated/account/assets"), "assets", "list")
        isolated_symbols = sorted({str(row.get("symbol", "")).upper() for row in isolated_assets if row.get("symbol")})
        for symbol in isolated_symbols:
            # Isolated Margin has its own borrow/repay ledgers. The generic
            # financial-records endpoint does not replace these: it omits the
            # loan ID and can combine the principal with adjacent account
            # entries. Keep this source separate and retain its symbol.
            for record in self._v2_windowed(
                "/api/v2/margin/isolated/borrow-history",
                {"symbol": symbol},
                start_ms,
                now_ms,
                record_keys=("resultList",),
                id_key="loanId",
                optional=True,
            ):
                payload = {**record, "_kind": "margin_borrow", "_margin_mode": "isolated", "_margin_symbol": symbol}
                yield RawRecord(self.source_id, f"margin-borrow-isolated-{symbol}-{record.get('loanId', record.get('cTime'))}", _ms(record.get("cTime")), payload)
            for record in self._v2_windowed(
                "/api/v2/margin/isolated/repay-history",
                {"symbol": symbol},
                start_ms,
                now_ms,
                record_keys=("resultList",),
                id_key="repayId",
                optional=True,
            ):
                payload = {**record, "_kind": "margin_repay", "_margin_mode": "isolated", "_margin_symbol": symbol}
                yield RawRecord(self.source_id, f"margin-repay-isolated-{symbol}-{record.get('repayId', record.get('cTime'))}", _ms(record.get("cTime")), payload)
            for record in self._v2_windowed(
                "/api/v2/margin/isolated/financial-records",
                {"symbol": symbol},
                start_ms,
                now_ms,
                record_keys=("resultList",),
                id_key="marginId",
                optional=True,
            ):
                if _is_margin_internal_transfer(record):
                    payload = {**record, "_kind": "margin_internal_transfer", "_margin_mode": "isolated", "_margin_symbol": symbol}
                    yield RawRecord(self.source_id, f"margin-internal-transfer-isolated-{symbol}-{record.get('marginId')}", _ms(record.get("cTime")), payload)
                elif _is_additional_margin_financial(record):
                    payload = {**record, "_kind": "margin_financial", "_margin_mode": "isolated", "_margin_symbol": symbol}
                    yield RawRecord(self.source_id, f"margin-financial-isolated-{symbol}-{record.get('marginId')}", _ms(record.get("cTime")), payload)

        # Earn savings has both flexible and fixed terms.  The response is
        # envelope-shaped (resultList/endId) and exposes pay_interest rather
        # than the older "interest" spelling the connector previously used.
        for period_type in ("flexible", "fixed"):
            for order_type, kind in (
                ("subscribe", "earn_subscribe"),
                ("redeem", "earn_redeem"),
                ("pay_interest", "earn_interest"),
                ("deduction", "earn_deduction"),
            ):
                for record in self._v2_windowed(
                    "/api/v2/earn/savings/records",
                    {"periodType": period_type, "orderType": order_type},
                    start_ms,
                    now_ms,
                    record_keys=("resultList",),
                    id_key="orderId",
                    optional=True,
                ):
                    payload = {**record, "_kind": kind, "_periodType": period_type}
                    record_id = f"{order_type}-{record.get('orderId', record.get('ts', record.get('cTime')))}"
                    yield RawRecord(self.source_id, f"earn-{record_id}", _ms(record.get("ts") or record.get("cTime")), payload)

        # The account-bill ledger is Bitget's source of truth for product
        # activity not represented by fills or wallet records: earn rewards,
        # airdrops/rebates, P2P/fiat buys and sells, and Classic Convert. We
        # deliberately normalize only non-duplicating, documented types;
        # deposits, withdrawals, fills and their fees already have richer
        # dedicated evidence above.
        for record in self._v2_windowed(
            "/api/v2/spot/account/bills", {}, start_ms, now_ms, id_key="billId", optional=True
        ):
            if not _is_additional_spot_bill(record):
                continue
            payload = {**record, "_kind": "spot_bill"}
            yield RawRecord(self.source_id, f"spot-bill-{record.get('billId')}", _ms(record.get("cTime")), payload)

        # Classic futures fills contain execution ID, exact filled quantity,
        # realized P/L, and fee detail. Account bills alone cannot recover
        # that evidence. Use the account-wide fills endpoint: the similarly
        # named ``fill-history`` API is order-specific and rejects a request
        # without an order ID, silently losing all fills in an optional sync.
        for product in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            for fill in self._v2_windowed(
                "/api/v2/mix/order/fills",
                {"productType": product},
                start_ms,
                now_ms,
                record_keys=("fillList",),
                id_key="tradeId",
                optional=True,
            ):
                payload = {**fill, "_kind": "classic_futures_fill", "_productType": product}
                yield RawRecord(
                    self.source_id,
                    f"classic-futures-fill-{product}-{fill.get('tradeId')}",
                    _ms(fill.get("cTime")),
                    payload,
                )

        # Futures ("mix") account bill supplies funding and non-fill account
        # activity.  Do not ingest ordinary open/close bills as well: those
        # are the account-side echo of the execution records above and would
        # otherwise double count P/L or fees.
        for product in ("USDT-FUTURES", "COIN-FUTURES", "USDC-FUTURES"):
            for record in self._v2_windowed(
                "/api/v2/mix/account/bill",
                {"productType": product},
                start_ms,
                now_ms,
                window_ms=_CLASSIC_FUTURES_WINDOW_MS,
                record_keys=("bills",),
                id_key="billId",
                optional=True,
            ):
                if _is_futures_execution_bill(record):
                    continue
                payload = {**record, "_kind": "futures_bill", "_productType": product}
                yield RawRecord(self.source_id, f"futures-bill-{record.get('billId', record.get('cTime'))}", _ms(record.get("cTime")), payload)

        # Classic Crypto Loans are separate from cross/isolated Margin. Keep
        # their collateral and debt movements as a distinct ledger source;
        # otherwise a repaid loan can make the later balance snapshot look
        # inexplicably different from the reconstructed activity.
        for record in self._v2_numbered_windowed("/api/v2/earn/loan/borrow-history", {}, start_ms, now_ms, optional=True):
            payload = {**record, "_kind": "uta_loan_borrow", "_loan_mode": "classic"}
            yield RawRecord(self.source_id, f"classic-loan-borrow-{record.get('orderId')}", _ms(record.get("borrowTime")), payload)
        for record in self._v2_numbered_windowed("/api/v2/earn/loan/repay-history", {}, start_ms, now_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('repayTime')}"
            parts: list[str] = []
            if _positive_amount(record.get("repayLoanAmount")):
                parts.append("principal")
            if _positive_amount(record.get("payInterest")):
                parts.append("interest")
            if _positive_amount(record.get("repayUnlockAmount")) and "principal" not in parts:
                parts.append("collateral")
            for part in parts:
                payload = {**record, "_kind": f"uta_loan_repay_{part}", "_loan_mode": "classic"}
                yield RawRecord(self.source_id, f"classic-loan-repay-{record_id}-{part}", _ms(record.get("repayTime")), payload)
        for record in self._v2_numbered_windowed("/api/v2/earn/loan/reduces", {}, start_ms, now_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('reduceTime')}"
            payload = {**record, "_kind": "uta_loan_reduce", "_loan_mode": "classic"}
            yield RawRecord(self.source_id, f"classic-loan-reduce-{record_id}", _ms(record.get("reduceTime")), payload)
        for record in self._v2_numbered_windowed("/api/v2/earn/loan/revise-history", {}, start_ms, now_ms, optional=True):
            record_id = f"{record.get('orderId')}-{record.get('reviseTime')}"
            payload = {**record, "_kind": "uta_loan_pledge_adjustment", "_loan_mode": "classic"}
            yield RawRecord(self.source_id, f"classic-loan-pledge-{record_id}", _ms(record.get("reviseTime")), payload)

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
                address_from=payload.get("fromAddress"),
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
                address_from=payload.get("fromAddress"),
                address_to=payload.get("toAddress"),
                notes=f"Bitget withdrawal · {payload.get('orderId')}",
                fees=fees,
                withdrawal_id=payload.get("orderId"),
                tx_hash=payload.get("txId") or payload.get("trHash"),
            )

        if kind == "margin_borrow":
            return NormalizedEvent(
                event_type="MARGIN_BORROW",
                event_subtype=f"{payload.get('_margin_mode', 'cross')}_margin",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("borrowAmount", payload.get("amount", "0"))),
                source_label=self.account_label,
                notes=f"Bitget {payload.get('_margin_mode', 'cross')}-margin borrow · {payload.get('_margin_symbol', payload.get('loanId', ''))}",
                order_id=payload.get("loanId"),
            )

        if kind == "margin_repay":
            return NormalizedEvent(
                event_type="MARGIN_REPAY",
                event_subtype=f"{payload.get('_margin_mode', 'cross')}_margin",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("repayAmount", payload.get("amount", "0"))),
                source_label=self.account_label,
                notes=f"Bitget {payload.get('_margin_mode', 'cross')}-margin repay · {payload.get('_margin_symbol', payload.get('repayId', ''))}",
                order_id=payload.get("repayId"),
            )

        if kind == "margin_financial":
            margin_type = str(payload.get("marginType", "")).lower()
            amount_raw = str(payload.get("amount", "0"))
            direction = "-" if amount_raw.strip().startswith("-") else "+"
            event_type = {
                "deal_in": "BUY",
                "deal_out": "SELL",
                "liquidation_fee": "LIQUIDATION",
                "confiscated": "LIQUIDATION",
                "compensate": "INCOME",
                "exchange_in": "BUY",
                "exchange_out": "SELL",
                "sys_exchange_in": "BUY",
                "sys_exchange_out": "SELL",
            }.get(margin_type, "UNKNOWN")
            if margin_type in {"deal_in", "compensate", "exchange_in", "sys_exchange_in"}:
                direction = "+"
            elif margin_type in {"deal_out", "liquidation_fee", "confiscated", "exchange_out", "sys_exchange_out"}:
                direction = "-"
            fees = _bill_fees(payload)
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"margin:{payload.get('_margin_mode', 'unspecified')}:{margin_type or 'unspecified'}",
                direction=direction,
                status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=_positive_amount(amount_raw) or "0",
                source_label=self.account_label,
                notes=(
                    f"Bitget {payload.get('_margin_mode', 'margin')} Margin "
                    f"{margin_type.replace('_', ' ') or 'financial record'} · "
                    f"{payload.get('_margin_symbol', '') or payload.get('marginId', '')}"
                ),
                fees=fees,
                order_id=str(payload.get("marginId")) if payload.get("marginId") is not None else None,
            )

        if kind == "margin_internal_transfer":
            transfer_type = str(payload.get("marginType", "")).lower()
            amount = _positive_amount(payload.get("amount")) or "0"
            asset = str(payload.get("coin", "")).upper()
            return NormalizedEvent(
                event_type="TRANSFER",
                event_subtype=f"bitget_margin_internal:{payload.get('_margin_mode', 'unspecified')}:{transfer_type or 'unspecified'}",
                direction="-" if transfer_type.endswith("out") else "+",
                status="COMPLETE" if transfer_type in {"transfer_in", "transfer_out"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=asset,
                amount=amount,
                source_label=self.account_label,
                notes=f"Bitget {payload.get('_margin_mode', 'margin')} Margin internal transfer · {transfer_type or 'unspecified'}",
                secondary_asset_symbol=asset or None,
                secondary_amount=amount,
                order_id=str(payload.get("marginId")) if payload.get("marginId") is not None else None,
            )

        if kind == "uta_internal_transfer":
            transfer_type = str(payload.get("type", "")).upper()
            amount = _positive_amount(payload.get("amount")) or "0"
            asset = str(payload.get("coin", "")).upper()
            return NormalizedEvent(
                event_type="TRANSFER",
                event_subtype=f"bitget_uta_internal:{payload.get('_category', 'unspecified').lower()}:{transfer_type.lower() or 'unspecified'}",
                direction="-" if transfer_type.endswith("_OUT") else "+",
                status="COMPLETE" if _is_uta_internal_transfer(payload) else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=asset,
                amount=amount,
                source_label=self.account_label,
                notes=f"Bitget UTA internal transfer · {transfer_type or 'unspecified'}",
                secondary_asset_symbol=asset or None,
                secondary_amount=amount,
                order_id=str(payload.get("id")) if payload.get("id") is not None else None,
            )

        if kind in ("earn_subscribe", "earn_redeem", "earn_interest", "earn_deduction"):
            event_type = {
                "earn_subscribe": "STAKING_DEPOSIT",
                "earn_redeem": "STAKING_WITHDRAWAL",
                "earn_interest": "STAKING_REWARD",
                # A fixed-term early-redemption deduction is a reduction of
                # earned yield, not a disposal of the deposited principal.
                "earn_deduction": "STAKING_REWARD",
            }[kind]
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"savings:{payload.get('_periodType', 'unspecified')}",
                direction="-" if kind in ("earn_subscribe", "earn_deduction") else "+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(
                    payload.get("coinName") or payload.get("settleCoinName") or payload.get("coin") or payload.get("productCoin") or ""
                ).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                notes=f"Bitget earn ({kind.split('_')[1]}) · {payload.get('orderId', '')}",
                order_id=payload.get("orderId"),
            )

        if kind == "spot_bill":
            business_type = str(payload.get("businessType", "")).upper()
            group_type = str(payload.get("groupType", "")).lower()
            amount_raw = str(payload.get("size", "0"))
            direction = "-" if amount_raw.startswith("-") else "+"
            if business_type == "DEPOSIT":
                event_type, direction = "DEPOSIT", "+"
            elif business_type == "WITHDRAW":
                event_type, direction = "WITHDRAWAL", "-"
            elif business_type == "TRANSFER_IN":
                event_type, direction = "TRANSFER", "+"
            elif business_type == "TRANSFER_OUT":
                event_type, direction = "TRANSFER", "-"
            elif business_type == "REBATE_REWARDS":
                event_type = "CASHBACK"
            elif business_type in {"AIRDROP_REWARDS", "USDT_CONTRACT_REWARDS", "MIX_CONTRACT_REWARDS"}:
                event_type = "AIRDROP"
            elif business_type in {"BATCH_INTEREST_USER_IN", "INTEREST", "INTEREST_SETTLEMENT"}:
                event_type = "STAKING_REWARD"
            else:
                # P2P, pre-market, fiat and Convert bills report one asset
                # movement per record. The stable business order ID links
                # their debit and credit records without inventing a quote
                # amount the API did not provide.
                event_type = "BUY" if direction == "+" else "SELL"
            fees = _bill_fees(payload)
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"bitget:{group_type or 'account_bill'}",
                direction=direction,
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=_positive_amount(amount_raw) or "0",
                source_label=self.account_label,
                notes=f"Bitget {group_type or 'account'} {business_type.replace('_', ' ').lower()} · {payload.get('bizOrderId', payload.get('billId', ''))}",
                fees=fees,
                secondary_asset_symbol=str(payload.get("coin", "")).upper() if event_type == "TRANSFER" else None,
                secondary_amount=_positive_amount(amount_raw) if event_type == "TRANSFER" else None,
                order_id=str(payload.get("bizOrderId") or payload.get("billId")) if payload.get("bizOrderId") or payload.get("billId") else None,
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

        if kind == "uta_futures_fill":
            category = str(payload.get("_category", ""))
            trade_side = str(payload.get("tradeSide", "")).lower()
            margin_asset = _futures_margin_asset(category, payload.get("symbol", ""))
            fees = _uta_fill_fees(payload)
            realized_pnl = _positive_amount(payload.get("execPnl"))

            if trade_side == "close" and realized_pnl:
                # Only a close settles P&L — that's the one moment a futures
                # fill actually changes real holdings (in the margin
                # currency). Opening a position just locks collateral you
                # already own; it isn't modeled as a holdings change here.
                pnl_is_loss = str(payload.get("execPnl", "0")).strip().startswith("-")
                return NormalizedEvent(
                    event_type="FUTURES_PNL",
                    event_subtype=f"futures:{category.lower()}",
                    direction="-" if pnl_is_loss else "+",
                    status="COMPLETE",
                    occurred_at=occurred_at,
                    original_timestamp=occurred_at.isoformat(),
                    asset_symbol=margin_asset,
                    amount=realized_pnl,
                    source_label=self.account_label,
                    notes=f"Bitget futures close · {payload.get('symbol')} · realized P&L",
                    fees=fees,
                    trade_id=payload.get("execId"),
                    order_id=payload.get("orderId"),
                )

            # Open, or a close with no realized P&L to report (break-even):
            # no confident holdings-affecting amount, but the fee (a real
            # cost) and the raw evidence are preserved either way rather
            # than silently discarded.
            return NormalizedEvent(
                event_type="FUTURES_OPEN" if trade_side == "open" else "FUTURES_CLOSE",
                event_subtype=f"futures:{category.lower()}",
                direction="-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=margin_asset,
                amount="0",
                source_label=self.account_label,
                notes=f"Bitget futures {trade_side or 'fill'} · {payload.get('symbol')} · qty {payload.get('execQty', '0')}",
                fees=fees,
                trade_id=payload.get("execId"),
                order_id=payload.get("orderId"),
            )

        if kind == "classic_futures_fill":
            product = str(payload.get("_productType", ""))
            trade_side = str(payload.get("tradeSide", "")).lower()
            margin_asset = str(payload.get("marginCoin") or _futures_margin_asset(product, payload.get("symbol", ""))).upper()
            realized_pnl = _positive_amount(payload.get("profit"))
            pnl_is_loss = str(payload.get("profit", "0")).strip().startswith("-")
            is_liquidation = any(marker in trade_side for marker in ("burst", "adl", "delivery"))
            is_close = is_liquidation or any(marker in trade_side for marker in ("close", "reduce"))
            fees = _spot_fees(payload)

            if is_close and realized_pnl:
                return NormalizedEvent(
                    event_type="LIQUIDATION" if is_liquidation else "FUTURES_PNL",
                    event_subtype=f"futures:{product.lower()}",
                    direction="-" if pnl_is_loss else "+",
                    status="COMPLETE",
                    occurred_at=occurred_at,
                    original_timestamp=occurred_at.isoformat(),
                    asset_symbol=margin_asset,
                    amount=realized_pnl,
                    source_label=self.account_label,
                    notes=f"Bitget Classic futures {'liquidation' if is_liquidation else 'close'} · {payload.get('symbol')} · realized P&L",
                    fees=fees,
                    trade_id=payload.get("tradeId"),
                    order_id=payload.get("orderId"),
                )

            return NormalizedEvent(
                event_type="FUTURES_CLOSE" if is_close else "FUTURES_OPEN",
                event_subtype=f"futures:{product.lower()}",
                direction="-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=margin_asset,
                amount="0",
                source_label=self.account_label,
                notes=f"Bitget Classic futures {trade_side or 'fill'} · {payload.get('symbol')} · qty {payload.get('baseVolume', payload.get('size', '0'))}",
                fees=fees,
                trade_id=payload.get("tradeId"),
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
                address_from=payload.get("fromAddress"),
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
            record_type = str(payload.get("type", ""))
            amount_raw = str(payload.get("amount", "0"))
            event_type, direction = _UTA_FINANCIAL_TYPE_MAP.get(
                record_type,
                ("UNKNOWN", "-" if amount_raw.strip().startswith("-") else "+"),
            )
            amount = _positive_amount(payload.get("amount")) or "0"
            return NormalizedEvent(
                event_type=event_type,
                event_subtype=f"uta:{str(payload.get('_category', '')).lower()}:{record_type.lower() or 'unspecified'}",
                direction=direction,
                status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=amount,
                source_label=self.account_label,
                notes=f"Bitget {record_type.replace('_', ' ').lower() or 'financial record'} · {payload.get('id', '')}",
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

        if kind == "uta_copy_transfer":
            to_lead = "lead" in {part.strip().lower() for part in str(payload.get("toType", "")).split(",")}
            from_lead = "lead" in {part.strip().lower() for part in str(payload.get("fromType", "")).split(",")}
            is_allocation = to_lead and not from_lead
            return NormalizedEvent(
                event_type="LENDING_DEPOSIT" if is_allocation else "LENDING_WITHDRAWAL",
                event_subtype="bitget_copy_trading_allocation",
                direction="-" if is_allocation else "+",
                status="COMPLETE" if str(payload.get("status", "")).lower() == "successful" and (to_lead or from_lead) else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("coin", "")).upper(),
                amount=str(payload.get("amount", "0")),
                source_label=self.account_label,
                notes=f"Bitget copy-trading {'allocation' if is_allocation else 'release'} · {payload.get('fromType', '')} → {payload.get('toType', '')}",
                order_id=payload.get("transferId"),
            )

        if kind == "uta_loan_borrow":
            status = str(payload.get("status", "")).lower()
            return NormalizedEvent(
                event_type="MARGIN_BORROW",
                event_subtype=f"crypto_loan:{str(payload.get('daily', 'flexible')).lower()}",
                direction="+",
                status="COMPLETE" if status not in {"rollback", "failed"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("loanCoin", "")).upper(),
                amount=str(payload.get("initLoanAmount", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan borrow · {payload.get('orderId', '')}",
                secondary_asset_symbol=str(payload.get("pledgeCoin", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("initPledgeAmount")),
                order_id=payload.get("orderId"),
            )

        if kind == "uta_loan_repay_principal":
            return NormalizedEvent(
                event_type="MARGIN_REPAY",
                event_subtype="crypto_loan",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("loanCoin", "")).upper(),
                amount=str(payload.get("repayLoanAmount", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan repayment · {payload.get('orderId', '')}",
                secondary_asset_symbol=str(payload.get("pledgeCoin", "")).upper() or None,
                secondary_amount=_positive_amount(payload.get("repayUnlockAmount")),
                order_id=payload.get("orderId"),
            )

        if kind == "uta_loan_repay_interest":
            return NormalizedEvent(
                event_type="MARGIN_INTEREST",
                event_subtype="crypto_loan",
                direction="-",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("loanCoin", "")).upper(),
                amount=str(payload.get("payInterest", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan interest · {payload.get('orderId', '')}",
                order_id=payload.get("orderId"),
            )

        if kind == "uta_loan_repay_collateral":
            return NormalizedEvent(
                event_type="LENDING_WITHDRAWAL",
                event_subtype="crypto_loan_collateral_release",
                direction="+",
                status="COMPLETE",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("pledgeCoin", "")).upper(),
                amount=str(payload.get("repayUnlockAmount", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan collateral release · {payload.get('orderId', '')}",
                order_id=payload.get("orderId"),
            )

        if kind == "uta_loan_reduce":
            fee = _positive_amount(payload.get("reduceFee"))
            fees = [
                NormalizedFee("EXCHANGE_FEE", str(payload.get("pledgeCoin", "")).upper(), fee)
            ] if fee else []
            return NormalizedEvent(
                event_type="LIQUIDATION",
                event_subtype="crypto_loan",
                direction="-",
                status="COMPLETE" if str(payload.get("status", "")).lower() == "complete" else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("pledgeCoin", "")).upper(),
                amount=str(payload.get("pledgeAmount", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan liquidation · {payload.get('orderId', '')}",
                fees=fees,
                order_id=payload.get("orderId"),
            )

        if kind == "uta_loan_pledge_adjustment":
            side = str(payload.get("reviseSide", "")).lower()
            is_lock = side == "down"
            return NormalizedEvent(
                event_type="LENDING_DEPOSIT" if is_lock else "LENDING_WITHDRAWAL",
                event_subtype="crypto_loan_collateral_adjustment",
                direction="-" if is_lock else "+",
                status="COMPLETE" if side in {"down", "up"} else "REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol=str(payload.get("pledgeCoin", "")).upper(),
                amount=str(payload.get("reviseAmount", "0")),
                source_label=self.account_label,
                notes=f"Bitget Crypto Loan collateral {'lock' if is_lock else 'release'} · {payload.get('orderId', '')}",
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
        asset = str(payload.get("coin", payload.get("marginCoin", ""))).upper()
        amount = str(amount_raw).lstrip("-") or "0"
        return NormalizedEvent(
            event_type=event_type,
            event_subtype=f"futures:{biz or 'unspecified'}",
            direction="+" if not str(amount_raw).startswith("-") else "-",
            status="COMPLETE" if event_type != "UNKNOWN" else "REQUIRES_REVIEW",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol=asset,
            amount=amount,
            source_label=self.account_label,
            notes=f"Bitget futures {payload.get('_productType', '')} · {biz or 'unrecognized business type'} · {payload.get('billId', '')}",
            secondary_asset_symbol=asset if event_type == "TRANSFER" else None,
            secondary_amount=amount if event_type == "TRANSFER" else None,
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


def _futures_margin_asset(category: str, symbol: str) -> str:
    """The currency a futures fill's P&L/fee actually settles in.
    USDT-/USDC-FUTURES settle in their quote coin (BTCUSDT -> USDT); COIN-
    FUTURES are inverse contracts settling in the base coin itself
    (BTCUSD -> BTC, not USD — _pair_assets can't help here since "USD"
    alone isn't in _QUOTE_ASSETS)."""
    if category == "USDT-FUTURES":
        return "USDT"
    if category == "USDC-FUTURES":
        return "USDC"
    normalized = str(symbol or "").upper()
    for suffix in ("USDT", "USDC", "USD"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


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


def _aggregate_balances(records: Iterable[dict]) -> list[Balance]:
    """Combine asset rows from Bitget's trading/funding balance endpoints.

    Some responses provide a total ``balance`` while others split it into
    available/frozen/locked values.  Prefer the exchange-supplied total to
    avoid counting the same amount twice, then aggregate the independently
    held funding and trading entries by coin.
    """
    totals: dict[str, Decimal] = {}
    for record in records:
        symbol = str(record.get("coin") or record.get("marginCoin") or "").upper()
        if not symbol:
            continue
        raw_total = record.get("balance")
        try:
            if raw_total in (None, ""):
                # Classic Margin's net figure includes debt and accrued
                # interest. Futures may provide equity instead. Prefer either
                # exchange-calculated total before falling back to free/locked
                # fields, which are only a gross balance.
                raw_total = record.get("net")
            if raw_total in (None, ""):
                raw_total = record.get("equity", record.get("accountEquity"))
            if raw_total in (None, ""):
                raw_total = (
                    Decimal(str(record.get("available", "0")))
                    + Decimal(str(record.get("frozen", "0")))
                    + Decimal(str(record.get("locked", "0")))
                )
            total = Decimal(str(raw_total))
        except (InvalidOperation, ValueError):
            continue
        if total > 0:
            totals[symbol] = totals.get(symbol, Decimal("0")) + total
    return [Balance(symbol, format(amount, "f")) for symbol, amount in sorted(totals.items()) if amount > 0]


def _is_additional_spot_bill(record: dict) -> bool:
    """Return only Classic account-bill rows not already represented by the
    dedicated fills/deposit/withdrawal endpoints.

    This avoids the common and damaging pattern of importing an order fill,
    then importing the matching spot account debit/credit and fee again.
    """
    business_type = str(record.get("businessType", "")).upper()
    group_type = str(record.get("groupType", "")).lower()
    if business_type in {
        "REBATE_REWARDS",
        "AIRDROP_REWARDS",
        "USDT_CONTRACT_REWARDS",
        "MIX_CONTRACT_REWARDS",
        "BATCH_INTEREST_USER_IN",
        "INTEREST",
        "INTEREST_SETTLEMENT",
    }:
        return True
    if group_type in {"convert", "c2c", "pre_c2c"} and business_type in {"BUY", "SELL"}:
        return True
    # Fiat cash movements do not appear in the crypto wallet deposit/
    # withdrawal endpoints. Keep them as their own ledger events instead of
    # silently treating a bank transfer as a crypto trade.
    if group_type in {"fait", "fiat"} and business_type in {"BUY", "SELL", "DEPOSIT", "WITHDRAW"}:
        return True
    # Spot account-bill transfers are funding/spot wallet reshuffles within
    # this one Bitget account. Preserve them for audit, but normalize paired
    # same-asset legs so they cannot change portfolio holdings.
    return group_type == "transfer" and business_type in {"TRANSFER_IN", "TRANSFER_OUT"}


def _is_additional_margin_financial(record: dict) -> bool:
    """Whether a Classic Margin financial row adds evidence not supplied by
    the dedicated borrow/repay endpoints or a zero-sum wallet transfer."""
    return str(record.get("marginType", "")).lower() not in {
        "borrow",
        "repay",
        "transfer_in",
        "transfer_out",
    }


def _is_margin_internal_transfer(record: dict) -> bool:
    return str(record.get("marginType", "")).lower() in {"transfer_in", "transfer_out"}


def _is_uta_internal_transfer(record: dict) -> bool:
    return str(record.get("type", "")).upper() in {
        "TRANSFER_IN", "TRANSFER_OUT", "RESERVE_TRANSFER_IN", "RESERVE_TRANSFER_OUT",
        "FINANCIAL_TRANSFER_IN", "FINANCIAL_TRANSFER_OUT", "CONVERT_TRANSFER_IN", "CONVERT_TRANSFER_OUT",
        "MARGIN_BACK", "INCREASE_MARGIN", "REDUCE_MARGIN",
    }


def _is_additional_uta_financial(record: dict) -> bool:
    """Exclude financial-record echoes already represented by richer fill,
    convert, or wallet evidence; retain every other type, including unknown
    future products, as reviewable Activity evidence."""
    return str(record.get("type", "")).upper() not in {
        # Spot/Margin fill debit/credit and fee are richer in /trade/fills.
        "ORDER_DEALT_FROZEN_OUT",
        "ORDER_DEALT_IN",
        "ORDER_PLF_FEE_OUT",
        # Futures fill history captures execution P/L, fee, order and trade
        # identifiers. These are its account-ledger echoes.
        "OPEN_LONG", "OPEN_SHORT", "BUY_DEAL", "SELL_DEAL", "CLOSE_LONG", "CLOSE_SHORT",
        "FORCE_CLOSE_LONG", "FORCE_CLOSE_SHORT", "BURST_CLOSE_LONG", "BURST_CLOSE_SHORT",
        "OFFSET_REDUCE_CLOSE_LONG", "OFFSET_REDUCE_CLOSE_SHORT", "FORCE_BUY_SSM", "FORCE_SELL_SSM",
        "BURST_BUY_SSM", "BURST_SELL_SSM", "MARGIN_OPEN_LONG", "MARGIN_OPEN_SHORT",
        "MARIN_BUY_DEAL", "MARIN_SELL_DEAL", "FIXED_OFFSET_IN_SSM_LONG", "FIXED_OFFSET_IN_SSM_SHORT",
        "FIXED_CLOSE_LONG", "FIXED_CLOSE_SHORT", "FIXED_FORCE_CLOSE_LONG", "FIXED_FORCE_CLOSE_SHORT",
        "FIXED_BURST_CLOSE_LONG", "FIXED_BURST_CLOSE_SHORT", "FIXED_ADL_CLOSE_LONG", "FIXED_ADL_CLOSE_SHORT",
        # These are intra-UTA wallet moves or detailed by their own history
        # endpoint; modeling only one side would distort holdings.
        "TRANSFER_IN", "TRANSFER_OUT", "RESERVE_TRANSFER_IN", "RESERVE_TRANSFER_OUT",
        "FINANCIAL_TRANSFER_IN", "FINANCIAL_TRANSFER_OUT", "CONVERT_TRANSFER_IN", "CONVERT_TRANSFER_OUT",
        "ON_CHAIN_TRANSFER_REFUND", "ON_CHAIN_TRANSFER_OUT", "WITHDRAW_TRANSFER_OUT", "WITHDRAW_TRANSFER_IN",
        "TRACE_LOCK_USER_OUT", "TRACE_LOCK_USER_IN", "TRACE_TRANSFER_USER_OUT", "TRACE_TRANSFER_USER_IN",
        "TRACE_TRANSFER_REFUND_IN", "MARGIN_BACK", "INCREASE_MARGIN", "REDUCE_MARGIN",
        "MARGIN_LEVER_ORDER_REFROZEN", "MARGIN_LEVER_ORDER_FROZEN", "MARGIN_LEVER_POS_IN",
    }


def _is_futures_execution_bill(record: dict) -> bool:
    """Classic futures bills duplicate the matched execution for these
    business types. The dedicated account-fills endpoint carries richer
    order/trade/fee data and is therefore the canonical import path."""
    business_type = str(record.get("businessType") or record.get("bizType") or "").lower()
    return business_type in {
    "open_position",
        "close_position",
        "open_long",
        "open_short",
        "close_long",
        "close_short",
        "force_close_long",
        "force_close_short",
    }


def _bill_fees(payload: dict) -> list[NormalizedFee]:
    amount = _positive_amount(payload.get("fees") or payload.get("fee"))
    if not amount:
        return []
    return [
        NormalizedFee(
            fee_type="EXCHANGE_FEE",
            asset_symbol=str(payload.get("coin", "")).upper(),
            amount=amount,
        )
    ]


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
