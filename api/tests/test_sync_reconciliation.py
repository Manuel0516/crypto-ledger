from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.connectors.base import Balance, ConnectorUnavailable, NormalizedEvent, RawRecord
from app.core.ledger.sync import sync_account
from app.db.models import Account, Base, Issue

OCCURRED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)


class _StubConnector:
    """A minimal Connector: yields one BTC deposit, then reports a live
    balance that either matches or diverges from it."""

    source_id = "stub"
    version = "stub-0.1"

    def __init__(self, live_amount: str, *, raise_on_balances: bool = False):
        self._live_amount = live_amount
        self._raise_on_balances = raise_on_balances

    def fetch(self, since=None):
        yield RawRecord(self.source_id, "dep-1", OCCURRED_AT, {})

    def normalize(self, raw):
        return NormalizedEvent(
            event_type="DEPOSIT",
            event_subtype="exchange",
            direction="+",
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            original_timestamp=OCCURRED_AT.isoformat(),
            asset_symbol="BTC",
            amount="1.0",
            account_name="Stub",
        )

    def fetch_balances(self):
        if self._raise_on_balances:
            raise ConnectorUnavailable("balance check unreachable")
        return [Balance("BTC", self._live_amount)]


class _NoBalanceStubConnector(_StubConnector):
    fetch_balances = None  # type: ignore[assignment]


class SyncReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _account(self) -> Account:
        account = Account(name="Stub Exchange", kind="exchange", connector_type="binance_live", status="not_configured")
        self.session.add(account)
        self.session.flush()
        return account

    def _mismatch_issues(self) -> list[Issue]:
        # Sync can also raise an unrelated "Missing price" Issue for a
        # synthetic test event with no valuation data — filter to the
        # balance-mismatch issue specifically rather than assume it's the
        # only one in the session.
        return [i for i in self.session.query(Issue).filter_by(resolved=False).all() if "balance doesn't match" in i.title]

    def test_sync_runs_reconciliation_and_flags_mismatch_as_issue(self) -> None:
        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_StubConnector("0.4")):
            result = sync_account(self.session, account, backfill=True)

        self.assertEqual(result.status, "ok")
        self.assertIsNotNone(result.reconciliation)
        self.assertEqual(result.reconciliation.status, "ok")
        self.assertEqual(len(result.reconciliation.assets), 1)
        self.assertFalse(result.reconciliation.assets[0].matches)

        issues = self._mismatch_issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("Stub Exchange", issues[0].title)
        self.assertIn("BTC", issues[0].detail)

    def test_sync_resolves_issue_once_balances_match_again(self) -> None:
        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_StubConnector("0.4")):
            sync_account(self.session, account, backfill=True)
        self.assertEqual(len(self._mismatch_issues()), 1)

        with patch("app.core.ledger.sync.build_connector", return_value=_StubConnector("1.0")):
            sync_account(self.session, account)
        self.assertEqual(len(self._mismatch_issues()), 0)

    def test_sync_does_not_duplicate_issue_across_repeated_mismatches(self) -> None:
        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_StubConnector("0.4")):
            sync_account(self.session, account, backfill=True)
            sync_account(self.session, account)
        self.assertEqual(len(self._mismatch_issues()), 1)

    def test_reconciliation_failure_does_not_break_sync(self) -> None:
        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_StubConnector("1.0", raise_on_balances=True)):
            result = sync_account(self.session, account, backfill=True)
        self.assertEqual(result.status, "ok")
        # reconcile_account() itself catches ConnectorUnavailable and returns
        # an "unavailable" ReconcileResult rather than raising — sync surfaces
        # that as-is, it just never turns it into an Issue or fails the sync.
        self.assertIsNotNone(result.reconciliation)
        self.assertEqual(result.reconciliation.status, "unavailable")
        self.assertEqual(len(self._mismatch_issues()), 0)

    def test_connector_without_balances_is_skipped_silently(self) -> None:
        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_NoBalanceStubConnector("1.0")):
            result = sync_account(self.session, account, backfill=True)
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.reconciliation)

    def test_backfill_surfaces_history_limit_note(self) -> None:
        account = self._account()
        connector = _StubConnector("1.0")
        connector.history_limit_note = "only 90 days available"  # type: ignore[attr-defined]
        with patch("app.core.ledger.sync.build_connector", return_value=connector):
            result = sync_account(self.session, account, backfill=True)
        self.assertEqual(result.message, "only 90 days available")

    def test_incremental_sync_does_not_repeat_history_limit_note(self) -> None:
        account = self._account()
        connector = _StubConnector("1.0")
        connector.history_limit_note = "only 90 days available"  # type: ignore[attr-defined]
        with patch("app.core.ledger.sync.build_connector", return_value=connector):
            result = sync_account(self.session, account, backfill=False)
        self.assertIsNone(result.message)

    def test_unexpected_connector_failure_becomes_retryable_sync_result(self) -> None:
        account = self._account()
        # The real API commits the account before starting its initial
        # backfill. Mirror that transaction boundary so rollback can preserve
        # the saved source while recording the failure Issue.
        self.session.commit()
        with patch("app.core.ledger.sync.build_connector", side_effect=RuntimeError("malformed Binance response")):
            result = sync_account(self.session, account, backfill=True)

        self.assertEqual(result.status, "unavailable")
        self.assertIn("malformed Binance response", result.message or "")
        saved_account = self.session.get(Account, account.id)
        self.assertIsNotNone(saved_account)
        self.assertEqual(saved_account.status, "error")
        issue = self.session.query(Issue).filter(Issue.title.contains("sync failed unexpectedly")).one()
        self.assertIn("malformed Binance response", issue.detail)

    def test_repeated_unexpected_failures_refresh_one_issue_instead_of_piling_up(self) -> None:
        # A source with a permanent problem (bad credentials, an invalid
        # address) that a scheduler keeps retrying every few minutes must
        # not leave one issue per attempt — only the latest detail matters.
        account = self._account()
        self.session.commit()
        with patch("app.core.ledger.sync.build_connector", side_effect=RuntimeError("bad request")):
            sync_account(self.session, account, backfill=True)
            sync_account(self.session, account)
            sync_account(self.session, account)
        issues = self.session.query(Issue).filter(Issue.title.contains("sync failed unexpectedly")).all()
        self.assertEqual(len(issues), 1)

    def test_repeated_connector_unavailable_refreshes_one_issue(self) -> None:
        account = self._account()
        self.session.commit()
        with patch("app.core.ledger.sync.build_connector", side_effect=ConnectorUnavailable("host down")):
            sync_account(self.session, account, backfill=True)
            sync_account(self.session, account)
        issues = self.session.query(Issue).filter(Issue.title.contains("is not reachable")).all()
        self.assertEqual(len(issues), 1)

    def test_repeated_stopped_early_failures_refresh_one_issue(self) -> None:
        class _FlakyConnector(_StubConnector):
            def fetch(self, since=None):
                yield RawRecord(self.source_id, "dep-1", OCCURRED_AT, {})
                raise ConnectorUnavailable("rate limited")

        account = self._account()
        with patch("app.core.ledger.sync.build_connector", return_value=_FlakyConnector("1.0")):
            sync_account(self.session, account, backfill=True)
            sync_account(self.session, account)
        issues = self.session.query(Issue).filter(Issue.title.contains("sync stopped early")).all()
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()
