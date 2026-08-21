from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.events import FeeIn, ManualEventIn, create_manual_event, get_event
from app.core.ledger.reconcile import compute_account_holdings
from app.db.models import Account, Base, Event, Fee


OCCURRED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class ManualOpeningBalanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.account = Account(name="Manual Monero", kind="manual", connector_type="manual", status="not_configured")
        self.session.add(self.account)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_opening_balance_is_an_account_event_and_future_activity_reduces_it(self) -> None:
        with patch("app.core.ledger.service.refresh_valuations"):
            create_manual_event(
                ManualEventIn(
                    event_subtype="opening_balance",
                    symbol="XMR",
                    amount="10.5",
                    occurred_at=OCCURRED_AT.isoformat(),
                    account_id=self.account.id,
                ),
                self.session,
            )
            create_manual_event(
                ManualEventIn(
                    event_type="WITHDRAWAL",
                    symbol="XMR",
                    amount="-2.5",
                    occurred_at=datetime(2026, 8, 22, tzinfo=timezone.utc).isoformat(),
                    account_id=self.account.id,
                ),
                self.session,
            )

        event = self.session.query(Event).filter_by(event_subtype="opening_balance").one()
        self.assertEqual(event.account_id, self.account.id)
        self.assertEqual(event.event_type, "MANUAL_ADJUSTMENT")
        self.assertEqual(compute_account_holdings(self.session, self.account.id)[("XMR", "Monero", None)], 8)

    def test_opening_balance_requires_a_manual_account_and_cannot_be_duplicated(self) -> None:
        exchange = Account(name="Exchange", kind="exchange", connector_type="binance_live", status="connected")
        self.session.add(exchange)
        self.session.flush()

        with self.assertRaisesRegex(HTTPException, "manual linked accounts"):
            create_manual_event(
                ManualEventIn(
                    event_subtype="opening_balance",
                    symbol="XMR",
                    amount="1",
                    occurred_at=OCCURRED_AT.isoformat(),
                    account_id=exchange.id,
                ),
                self.session,
            )

        with patch("app.core.ledger.service.refresh_valuations"):
            body = ManualEventIn(
                event_subtype="opening_balance",
                symbol="XMR",
                amount="1",
                occurred_at=OCCURRED_AT.isoformat(),
                account_id=self.account.id,
            )
            create_manual_event(body, self.session)
            with self.assertRaisesRegex(HTTPException, "already has an opening balance"):
                create_manual_event(body, self.session)

    def test_manual_entry_rejects_legacy_activity_types(self) -> None:
        with self.assertRaises(ValidationError):
            ManualEventIn(
                event_type="STOLEN",
                symbol="BTC",
                amount="1",
                occurred_at=OCCURRED_AT.isoformat(),
            )

    def test_manual_activity_uses_one_canonical_fees_collection(self) -> None:
        with patch("app.core.ledger.service.refresh_valuations"):
            body = ManualEventIn(
                event_type="WITHDRAWAL",
                symbol="ETH",
                amount="-1",
                occurred_at=OCCURRED_AT.isoformat(),
                account_id=self.account.id,
                fees=[FeeIn(fee_type="GAS_FEE", asset_symbol="ETH", amount="0.01")],
            )
            event_result = create_manual_event(body, self.session)

        event = self.session.get(Event, event_result["id"])
        self.assertIsNotNone(event)
        self.assertEqual(self.session.query(Fee).filter_by(event_id=event.id).count(), 1)
        detail = get_event(event.id, self.session)
        self.assertEqual(len(detail["event"]["fees"]), 1)
        self.assertNotIn("fees", detail)

        with self.assertRaises(ValidationError):
            ManualEventIn(
                event_type="WITHDRAWAL",
                symbol="ETH",
                amount="-1",
                occurred_at=OCCURRED_AT.isoformat(),
                fee_asset="ETH",
                fee_amount="0.01",
            )


if __name__ == "__main__":
    unittest.main()
