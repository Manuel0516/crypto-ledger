from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from nostr_sdk import (
    GetInfoResponse,
    ListTransactionsRequest,
    LookupInvoiceResponse,
    NostrSdkError,
    NostrWalletConnect,
    NostrWalletConnectUri,
    Timestamp,
    TransactionState,
)

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, NormalizedFee, RawRecord

# Nostr Wallet Connect (NIP-47) — a generic Lightning wallet integration, not
# tied to any one wallet app. ZEUS is the first wallet configured through it
# (see docs/lightning-nwc.md for why, and how another NWC wallet — Alby,
# Mutiny, ... — plugs into this exact same connector with zero code changes:
# "wallet software" is UI metadata (Account.wallet_software), never a code
# branch here).
#
# Crypto (BIP-340 Schnorr signing, NIP-04/NIP-44 encryption) is delegated to
# `nostr-sdk` (rust-nostr's official Python bindings) rather than hand-rolled
# — this app must never become capable of a subtly-wrong signature or a
# broken encryption padding scheme on a live Lightning wallet connection.
#
# SECURITY — read before touching this file: this connector is READ-ONLY by
# construction, not by permission-checking. The only NWC methods ever
# invoked anywhere below are get_info, get_balance, and list_transactions.
# pay_invoice / pay_keysend / make_invoice / *_hold_invoice are never called
# — not gated behind a flag, simply absent from this module's code, so no
# code path here could spend funds even if a connection happened to grant
# those permissions. _SAFE_METHOD_NAMES below exists only to warn the user
# their connection over-grants permissions this app doesn't need — never to
# use them. Never log or format the raw NostrWalletConnectUri object or a
# SecretKey — str(uri) reconstructs the full connection string, secret
# included; only ever surface `.public_key().to_hex()` (a public key).

# NIP-47's whitelist of methods this connector actually needs. Anything a
# connection grants beyond this set — including a method name this SDK
# version doesn't recognize (UNKNOWN) — is treated as an extra capability to
# warn about, never silently trusted as safe (default-deny, not a blocklist
# of specific "dangerous" names that could miss a future method).
_SAFE_METHOD_NAMES = {"GET_INFO", "GET_BALANCE", "LIST_TRANSACTIONS", "LOOKUP_INVOICE"}

# 1 BTC = 100_000_000 sat = 100_000_000_000 msat. Exact integer-denominator
# division via Decimal — never float — so a msat amount round-trips exactly.
_MSAT_PER_BTC = Decimal(10) ** 11

# Terminal-only: a pending/accepted (hold-invoice-in-flight) transaction is
# not yet a fact, and re-syncing it once it does resolve would be silently
# deduped by (source_id, external_id) rather than updated — RawEvent rows
# are never mutated in this codebase. So, same as the Bitcoin connector
# waiting for on-chain confirmation, an unresolved transaction is simply not
# yielded yet; it appears on a later sync once it reaches SETTLED or FAILED.
_TERMINAL_STATES = {TransactionState.SETTLED, TransactionState.FAILED, TransactionState.EXPIRED}


def parse_connection_uri(connection_string: str) -> NostrWalletConnectUri:
    """Validates a pasted NWC connection string without connecting. Raises
    ConnectorUnavailable (not a raw SDK exception) on anything malformed, so
    the API layer can show the user a message instead of a stack trace."""
    try:
        return NostrWalletConnectUri.parse(connection_string.strip())
    except NostrSdkError as exc:
        raise ConnectorUnavailable("That doesn't look like a valid NWC connection string.") from exc


@dataclass(frozen=True)
class NWCPermissions:
    """What a connection is actually permitted to do, per its own get_info
    response — not assumed, not configured by this app."""

    methods: list[str]
    extra_methods: list[str]  # granted but outside _SAFE_METHOD_NAMES — never called, only ever surfaced as a warning

    @property
    def has_spend_capability(self) -> bool:
        return bool(self.extra_methods)


def _method_name(method) -> str:
    # nostr_sdk's Method is a tagged union where each variant is its own
    # nested class (Method.GET_INFO, Method.PAY_INVOICE, ...); an instance's
    # class __name__ is qualified as "Method.GET_INFO" — the segment after
    # the dot is the method name NIP-47 itself uses.
    return type(method).__name__.rsplit(".", 1)[-1]


def describe_permissions(info: GetInfoResponse) -> NWCPermissions:
    names = sorted({_method_name(m) for m in (info.methods or [])})
    extra = [n for n in names if n not in _SAFE_METHOD_NAMES]
    return NWCPermissions(methods=names, extra_methods=extra)


def _to_timestamp(value: datetime) -> Timestamp:
    return Timestamp.from_secs(int(value.timestamp()))


def _to_datetime(value: Timestamp | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value.as_secs(), tz=timezone.utc)


def _msat_to_btc(value_msat: int | None) -> str:
    return str(Decimal(value_msat or 0) / _MSAT_PER_BTC)


def _serialize_transaction(tx: LookupInvoiceResponse) -> dict:
    """The raw evidence payload — every field NIP-47 exposed for this
    transaction, tolerant of whichever ones a given wallet service left
    unset (NIP-47 marks most fields optional; different implementations
    populate different subsets). No secrets pass through here: a payment
    preimage is included when present, since knowing a *payment you made*
    completed is not a credential — it can't be used to move funds, only to
    prove one specific payment happened, and NIP-47 returns it directly to
    the paying client for that reason."""
    return {
        "transaction_type": tx.transaction_type.name if tx.transaction_type is not None else None,
        "state": tx.state.name if tx.state is not None else None,
        "invoice": tx.invoice,
        "description": tx.description,
        "description_hash": tx.description_hash,
        "preimage": tx.preimage,
        "payment_hash": tx.payment_hash,
        "amount_msat": tx.amount,
        "fees_paid_msat": tx.fees_paid,
        "created_at": tx.created_at.as_secs() if tx.created_at is not None else None,
        "expires_at": tx.expires_at.as_secs() if tx.expires_at is not None else None,
        "settled_at": tx.settled_at.as_secs() if tx.settled_at is not None else None,
        "metadata": _json_value_to_py(tx.metadata),
    }


def _json_value_to_py(value) -> object:
    """nostr-sdk's JsonValue is its own opaque type, not a plain dict/list —
    round-trip it through its own JSON string form so raw evidence storage
    (which needs plain-JSON-serializable Python objects) doesn't choke on it
    or silently drop metadata a wallet attached (zap/boostagram details,
    etc. per NIP-47)."""
    if value is None:
        return None
    import json as _json

    try:
        return _json.loads(value.as_json())
    except Exception:
        return None


class NWCConnector:
    """Generic Nostr Wallet Connect (NIP-47) Lightning connector. One
    instance is one NWC connection (one wallet). Never signs or sends a
    payment — see the module docstring above."""

    source_id = "lightning_nwc"

    def __init__(self, connection_string: str, account_label: str):
        # Deliberately not parsed here: construction must never raise (every
        # other connector in this codebase tolerates a blank/bad config at
        # __init__ and only fails on first actual use) — build_connector()
        # in connectors.py isn't wrapped in try/except, so an eager parse
        # failure here would surface as an unhandled 500 instead of the
        # clean SyncResult(status="unavailable", ...) every other connector
        # produces on bad credentials.
        self.connection_string = connection_string
        self.account_label = account_label
        self._uri: NostrWalletConnectUri | None = None

    @property
    def version(self) -> str:
        return "lightning-nwc-0.1"

    def _parsed_uri(self) -> NostrWalletConnectUri:
        if self._uri is None:
            self._uri = parse_connection_uri(self.connection_string)
        return self._uri

    def _namespace(self) -> str:
        return f"{self.source_id}:{self._parsed_uri().public_key().to_hex()[:16]}"

    def _client(self) -> NostrWalletConnect:
        return NostrWalletConnect(self._parsed_uri())

    def _run(self, coro):
        try:
            return asyncio.run(coro)
        except NostrSdkError as exc:
            raise ConnectorUnavailable(f"Could not reach the NWC wallet service: {exc}") from exc

    def test_connection(self) -> bool:
        self._run(self._client().get_info())
        return True

    def permissions(self) -> NWCPermissions:
        info = self._run(self._client().get_info())
        return describe_permissions(info)

    def fetch_balances(self) -> Iterable[Balance]:
        response = self._run(self._client().get_balance())
        amount = _msat_to_btc(response.balance)
        if Decimal(amount) == 0:
            return []
        return [Balance("BTC", amount)]

    def fetch(self, since: datetime | None = None) -> Iterable[RawRecord]:
        client = self._client()
        request = ListTransactionsRequest(
            _from=_to_timestamp(since) if since else None,
            until=None,
            limit=None,
            offset=None,
            unpaid=None,
            transaction_type=None,
        )
        transactions = self._run(client.list_transactions(request))
        for tx in transactions:
            if tx.state not in _TERMINAL_STATES:
                continue  # pending/accepted — not a fact yet, see _TERMINAL_STATES
            payload = _serialize_transaction(tx)
            external_id = tx.payment_hash or f"noHash-{tx.created_at.as_secs() if tx.created_at else 0}-{tx.amount}"
            occurred_at = _to_datetime(tx.settled_at) or _to_datetime(tx.created_at)
            yield RawRecord(self._namespace(), external_id, occurred_at, payload)

    def normalize(self, raw: RawRecord) -> NormalizedEvent:
        payload = raw.payload
        occurred_at = raw.source_timestamp or datetime.now(timezone.utc)
        state = payload.get("state")
        transaction_type = payload.get("transaction_type")  # "INCOMING" | "OUTGOING"
        is_incoming = transaction_type == "INCOMING"

        if state != "SETTLED":
            # FAILED or EXPIRED: preserved as evidence (never silently
            # discarded — see docs/lightning-nwc.md), but no funds actually
            # moved, so the canonical event must not claim otherwise.
            return NormalizedEvent(
                event_type="LIGHTNING_RECEIVE" if is_incoming else "LIGHTNING_SEND",
                event_subtype="lightning_nwc",
                direction="+" if is_incoming else "-",
                status="REQUIRES_REVIEW",
                occurred_at=occurred_at,
                original_timestamp=occurred_at.isoformat(),
                asset_symbol="BTC",
                amount="0",
                account_name=self.account_label,
                notes=f"Lightning payment {(state or 'unknown').lower()} — no funds moved · {payload.get('payment_hash', '')[:16]}",
                tx_hash=payload.get("payment_hash"),
                description=payload.get("description"),
                evidence_reference=payload.get("invoice"),
            )

        fees = []
        fee_msat = payload.get("fees_paid_msat")
        if fee_msat:
            fees.append(NormalizedFee(fee_type="LIGHTNING_FEE", asset_symbol="BTC", amount=_msat_to_btc(fee_msat)))

        return NormalizedEvent(
            event_type="LIGHTNING_RECEIVE" if is_incoming else "LIGHTNING_SEND",
            event_subtype="lightning_nwc",
            direction="+" if is_incoming else "-",
            status="COMPLETE",
            occurred_at=occurred_at,
            original_timestamp=occurred_at.isoformat(),
            asset_symbol="BTC",
            # No asset_network: BTC moved over Lightning is still BTC, the
            # same fungible holding as on-chain BTC (unlike a bridged token,
            # there is no separate "Lightning BTC" asset) — leaving this
            # unset resolves to the ledger's canonical "Bitcoin" network via
            # get_or_create_asset, so on-chain + Lightning balances sum into
            # one portfolio figure. event_subtype above still distinguishes
            # them in Activity.
            amount=_msat_to_btc(payload.get("amount_msat")),
            account_name=self.account_label,
            notes=f"Lightning {'receive' if is_incoming else 'payment'} · {payload.get('payment_hash', '')[:16]}",
            fees=fees,
            tx_hash=payload.get("payment_hash"),
            description=payload.get("description"),
            evidence_reference=payload.get("invoice"),
        )
