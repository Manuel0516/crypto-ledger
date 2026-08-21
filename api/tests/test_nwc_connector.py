from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from nostr_sdk import (
    GetBalanceResponse,
    GetInfoResponse,
    LookupInvoiceResponse,
    Method,
    NostrSdkError,
    Timestamp,
    TransactionState,
    TransactionType,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import ConnectorUnavailable, RawRecord
from app.connectors.lightning.nwc import NWCConnector, describe_permissions, parse_connection_uri
from app.core.ledger.service import ingest
from app.core.ledger.sync import sync_account
from app.db.models import Account, Asset, Base, Issue, Valuation

# A synthetic (non-real) connection string, structurally valid per NIP-47 —
# never a real wallet's credentials.
SYNTHETIC_URI = (
    "nostr+walletconnect://b889ff5b1513b641e2a139f661a661364979c5beee91842f8f0ef42ab558e9d4"
    "?relay=wss%3A%2F%2Frelay.example.test&secret=71a8c14c1407c113601079c4302dab36460f0ccd0ad506f1f2dc73b5100e4f3c"
)


def _tx(
    *,
    transaction_type: TransactionType,
    state: TransactionState | None,
    payment_hash: str | None,
    amount_msat: int,
    fees_paid_msat: int = 0,
    created_at: int = 1_700_000_000,
    settled_at: int | None = None,
    description: str | None = None,
    invoice: str | None = None,
    preimage: str | None = None,
    expires_at: int | None = None,
) -> LookupInvoiceResponse:
    return LookupInvoiceResponse(
        transaction_type=transaction_type,
        state=state,
        invoice=invoice,
        description=description,
        description_hash=None,
        preimage=preimage,
        payment_hash=payment_hash or "",
        amount=amount_msat,
        fees_paid=fees_paid_msat,
        created_at=Timestamp.from_secs(created_at),
        expires_at=Timestamp.from_secs(expires_at) if expires_at is not None else None,
        settled_at=Timestamp.from_secs(settled_at) if settled_at is not None else None,
        metadata=None,
    )


class _FakeClient:
    """Stands in for nostr_sdk.NostrWalletConnect — real async methods
    returning canned SDK response objects, so NWCConnector's own fetch/
    normalize/permission logic runs unmodified against real nostr_sdk types."""

    def __init__(self, *, info=None, balance=None, transactions=None, fail_with: Exception | None = None):
        self._info = info
        self._balance = balance
        self._transactions = transactions or []
        self._fail_with = fail_with

    async def get_info(self):
        if self._fail_with:
            raise self._fail_with
        return self._info

    async def get_balance(self):
        if self._fail_with:
            raise self._fail_with
        return self._balance

    async def list_transactions(self, params):
        if self._fail_with:
            raise self._fail_with
        return self._transactions


def _connector(**client_kwargs) -> NWCConnector:
    connector = NWCConnector(SYNTHETIC_URI, "ZEUS Lightning")
    connector._client = lambda: _FakeClient(**client_kwargs)  # type: ignore[method-assign]
    return connector


class NWCConnectorTests(unittest.TestCase):
    def test_parses_a_valid_connection_string(self) -> None:
        uri = parse_connection_uri(SYNTHETIC_URI)
        self.assertEqual(uri.public_key().to_hex(), "b889ff5b1513b641e2a139f661a661364979c5beee91842f8f0ef42ab558e9d4")

    def test_rejects_a_malformed_connection_string(self) -> None:
        with self.assertRaises(ConnectorUnavailable):
            parse_connection_uri("not a valid nwc uri")

    def test_successful_connection(self) -> None:
        connector = _connector(info=GetInfoResponse(
            alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
            methods=[Method.GET_INFO()], notifications=[],
        ))
        self.assertTrue(connector.test_connection())

    def test_connection_failure_surfaces_as_connector_unavailable(self) -> None:
        connector = _connector(fail_with=NostrSdkError.Generic("relay unreachable"))
        with self.assertRaises(ConnectorUnavailable):
            connector.test_connection()

    def test_permission_discovery_flags_only_non_observer_methods(self) -> None:
        info = GetInfoResponse(
            alias="Zeus", color=None, pubkey=None, network="mainnet", block_height=None, block_hash=None,
            methods=[Method.GET_INFO(), Method.GET_BALANCE(), Method.LIST_TRANSACTIONS()], notifications=[],
        )
        permissions = describe_permissions(info)
        self.assertEqual(permissions.methods, ["GET_BALANCE", "GET_INFO", "LIST_TRANSACTIONS"])
        self.assertEqual(permissions.extra_methods, [])
        self.assertFalse(permissions.has_spend_capability)

    def test_permission_discovery_warns_on_spend_capable_methods(self) -> None:
        info = GetInfoResponse(
            alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
            methods=[Method.GET_BALANCE(), Method.PAY_INVOICE(), Method.MAKE_INVOICE()], notifications=[],
        )
        permissions = describe_permissions(info)
        self.assertIn("PAY_INVOICE", permissions.extra_methods)
        self.assertIn("MAKE_INVOICE", permissions.extra_methods)
        self.assertTrue(permissions.has_spend_capability)

    def test_unrecognized_method_is_treated_as_extra_not_silently_safe(self) -> None:
        info = GetInfoResponse(
            alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
            methods=[Method.GET_BALANCE(), Method.UNKNOWN("sign_message")], notifications=[],
        )
        permissions = describe_permissions(info)
        self.assertTrue(permissions.has_spend_capability)

    def test_balance_retrieval_converts_msat_to_btc_without_float(self) -> None:
        connector = _connector(balance=GetBalanceResponse(balance=125_420_000))
        balances = list(connector.fetch_balances())
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0].asset_symbol, "BTC")
        self.assertEqual(Decimal(balances[0].amount), Decimal("125420000") / Decimal(10**11))

    def test_zero_balance_reports_no_balance_entries(self) -> None:
        connector = _connector(balance=GetBalanceResponse(balance=0))
        self.assertEqual(list(connector.fetch_balances()), [])

    def test_empty_history(self) -> None:
        connector = _connector(transactions=[])
        self.assertEqual(list(connector.fetch()), [])

    def test_received_payment_normalizes_to_lightning_receive(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.SETTLED,
            payment_hash="hash-in-1", amount_msat=125_420_000, settled_at=1_700_000_100,
            description="coffee",
        )])
        raw = list(connector.fetch())
        self.assertEqual(len(raw), 1)
        event = connector.normalize(raw[0])
        self.assertEqual(event.event_type, "LIGHTNING_RECEIVE")
        self.assertEqual(event.direction, "+")
        self.assertEqual(event.status, "COMPLETE")
        self.assertEqual(Decimal(event.amount), Decimal("125420000") / Decimal(10**11))
        self.assertIsNone(event.asset_network)  # resolves to canonical "Bitcoin", same identity as on-chain BTC
        self.assertEqual(event.tx_hash, "hash-in-1")

    def test_sent_payment_normalizes_to_lightning_send(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.OUTGOING, state=TransactionState.SETTLED,
            payment_hash="hash-out-1", amount_msat=12_000_000, settled_at=1_700_000_200,
        )])
        event = connector.normalize(list(connector.fetch())[0])
        self.assertEqual(event.event_type, "LIGHTNING_SEND")
        self.assertEqual(event.direction, "-")
        self.assertEqual(Decimal(event.amount), Decimal("12000000") / Decimal(10**11))

    def test_payment_with_fee_preserves_fee_on_the_same_event(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.OUTGOING, state=TransactionState.SETTLED,
            payment_hash="hash-out-fee", amount_msat=12_000_000, fees_paid_msat=7_000, settled_at=1_700_000_300,
        )])
        event = connector.normalize(list(connector.fetch())[0])
        self.assertEqual(len(event.fees), 1)
        self.assertEqual(event.fees[0].fee_type, "LIGHTNING_FEE")
        self.assertEqual(Decimal(event.fees[0].amount), Decimal("7000") / Decimal(10**11))

    def test_pending_payment_is_not_yielded_yet(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.OUTGOING, state=TransactionState.PENDING,
            payment_hash="hash-pending", amount_msat=5_000_000,
        )])
        self.assertEqual(list(connector.fetch()), [])

    def test_accepted_hold_invoice_is_not_yielded_yet(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.ACCEPTED,
            payment_hash="hash-accepted", amount_msat=5_000_000,
        )])
        self.assertEqual(list(connector.fetch()), [])

    def test_failed_payment_is_preserved_with_zero_amount(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.OUTGOING, state=TransactionState.FAILED,
            payment_hash="hash-failed", amount_msat=9_000_000, created_at=1_700_000_400,
        )])
        raw = list(connector.fetch())
        self.assertEqual(len(raw), 1)  # preserved, not discarded
        event = connector.normalize(raw[0])
        self.assertEqual(event.amount, "0")
        self.assertEqual(event.status, "REQUIRES_REVIEW")

    def test_expired_invoice_is_preserved_with_zero_amount(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.EXPIRED,
            payment_hash="hash-expired", amount_msat=3_000_000, created_at=1_700_000_500,
        )])
        event = connector.normalize(list(connector.fetch())[0])
        self.assertEqual(event.amount, "0")
        self.assertEqual(event.status, "REQUIRES_REVIEW")

    def test_missing_optional_fields_are_tolerated(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.SETTLED,
            payment_hash="hash-minimal", amount_msat=1_000, settled_at=1_700_000_600,
            description=None, invoice=None, preimage=None,
        )])
        event = connector.normalize(list(connector.fetch())[0])
        self.assertEqual(event.event_type, "LIGHTNING_RECEIVE")
        self.assertIsNone(event.description)

    def test_malformed_response_missing_state_does_not_crash(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=None,
            payment_hash="hash-no-state", amount_msat=1_000,
        )])
        self.assertEqual(list(connector.fetch()), [])  # no state -> not treated as terminal

    def test_historical_backfill_requests_no_lower_bound(self) -> None:
        captured = {}

        class _CapturingClient(_FakeClient):
            async def list_transactions(self, params):
                captured["from"] = params._from
                return []

        connector = NWCConnector(SYNTHETIC_URI, "ZEUS Lightning")
        connector._client = lambda: _CapturingClient()  # type: ignore[method-assign]
        list(connector.fetch(since=None))
        self.assertIsNone(captured["from"])

    def test_incremental_sync_requests_a_lower_bound(self) -> None:
        captured = {}

        class _CapturingClient(_FakeClient):
            async def list_transactions(self, params):
                captured["from"] = params._from
                return []

        connector = NWCConnector(SYNTHETIC_URI, "ZEUS Lightning")
        connector._client = lambda: _CapturingClient()  # type: ignore[method-assign]
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        list(connector.fetch(since=since))
        self.assertIsNotNone(captured["from"])
        self.assertEqual(captured["from"].as_secs(), int(since.timestamp()))

    def test_duplicate_synchronization_does_not_duplicate_raw_evidence(self) -> None:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.SETTLED,
            payment_hash="hash-dup", amount_msat=1_000_000, settled_at=1_700_000_700,
        )])
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            raw_records = list(connector.fetch())
            first = ingest(session, connector, raw_records[0])
            second = ingest(session, connector, raw_records[0])
            self.assertIsNotNone(first)
            self.assertIsNone(second)  # idempotent: already-stored evidence yields no new event
        finally:
            session.close()
            engine.dispose()


class NWCPricingIntegrationTests(unittest.TestCase):
    """Lightning events reuse the existing pricing pipeline as-is — these
    confirm that reuse actually happens end-to-end, not a parallel system."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _raw(self) -> RawRecord:
        connector = _connector(transactions=[_tx(
            transaction_type=TransactionType.INCOMING, state=TransactionState.SETTLED,
            payment_hash="hash-price", amount_msat=100_000_000, settled_at=1_700_001_000,
        )])
        return list(connector.fetch())[0], connector

    def test_eur_and_sek_valuation_attached_on_ingest(self) -> None:
        raw, connector = self._raw()
        with patch("app.core.ledger.service.get_historical_prices", return_value={
            "EUR": (Decimal("60000"), "DAILY_REFERENCE"),
            "SEK": (Decimal("650000"), "DAILY_REFERENCE"),
        }):
            event = ingest(self.session, connector, raw)
        valuations = {v.quote_currency: v for v in self.session.query(Valuation).filter_by(event_id=event.id)}
        self.assertIn("EUR", valuations)
        self.assertIn("SEK", valuations)

    def test_pricing_failure_does_not_block_ingestion(self) -> None:
        raw, connector = self._raw()
        with patch("app.core.ledger.service.get_historical_prices", side_effect=RuntimeError("provider down")):
            event = ingest(self.session, connector, raw)
        self.assertIsNotNone(event)  # the Lightning event itself is still recorded
        issues = self.session.query(Issue).filter_by(event_id=event.id, resolved=False).all()
        self.assertTrue(any("price" in i.title.lower() for i in issues))


class NWCSyncPermissionIssueTests(unittest.TestCase):
    """The security-relevant end of this feature: a connection that grants
    more than read access must be flagged, automatically, on every sync —
    never used, only warned about."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _account(self) -> Account:
        account = Account(name="ZEUS Lightning", kind="wallet", connector_type="lightning_nwc", status="not_configured")
        self.session.add(account)
        self.session.flush()
        return account

    def _mismatch_issues(self, keyword: str) -> list[Issue]:
        return [i for i in self.session.query(Issue).filter_by(resolved=False).all() if keyword in i.title]

    def test_spend_capable_connection_is_flagged_as_an_issue(self) -> None:
        account = self._account()
        connector = _connector(
            info=GetInfoResponse(
                alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
                methods=[Method.GET_BALANCE(), Method.PAY_INVOICE()], notifications=[],
            ),
            transactions=[],
        )
        with patch("app.core.ledger.sync.build_connector", return_value=connector):
            sync_account(self.session, account, backfill=True)
        issues = self._mismatch_issues("more than read access")
        self.assertEqual(len(issues), 1)
        self.assertIn("PAY_INVOICE", issues[0].detail)

    def test_read_only_connection_raises_no_issue(self) -> None:
        account = self._account()
        connector = _connector(
            info=GetInfoResponse(
                alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
                methods=[Method.GET_BALANCE(), Method.GET_INFO(), Method.LIST_TRANSACTIONS()], notifications=[],
            ),
            transactions=[],
        )
        with patch("app.core.ledger.sync.build_connector", return_value=connector):
            sync_account(self.session, account, backfill=True)
        self.assertEqual(self._mismatch_issues("more than read access"), [])

    def test_issue_resolves_once_reconnected_read_only(self) -> None:
        account = self._account()
        spend_capable = _connector(
            info=GetInfoResponse(
                alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
                methods=[Method.GET_BALANCE(), Method.PAY_INVOICE()], notifications=[],
            ),
            transactions=[],
        )
        with patch("app.core.ledger.sync.build_connector", return_value=spend_capable):
            sync_account(self.session, account, backfill=True)
        self.assertEqual(len(self._mismatch_issues("more than read access")), 1)

        read_only = _connector(
            info=GetInfoResponse(
                alias=None, color=None, pubkey=None, network=None, block_height=None, block_hash=None,
                methods=[Method.GET_BALANCE()], notifications=[],
            ),
            transactions=[],
        )
        with patch("app.core.ledger.sync.build_connector", return_value=read_only):
            sync_account(self.session, account)
        self.assertEqual(self._mismatch_issues("more than read access"), [])


if __name__ == "__main__":
    unittest.main()
