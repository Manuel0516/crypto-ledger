from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.events import AddSwapLegIn, add_swap_leg
from app.connectors.base import RawRecord
from app.connectors.manual import ManualConnector
from app.core.ledger.service import ingest
from app.db.models import Base, Event, Valuation


class SwapLegTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.connector = ManualConnector()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _withdrawal(self, external_id: str = "w1") -> Event:
        event = ingest(
            self.session,
            self.connector,
            RawRecord(
                "manual",
                external_id,
                datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
                {
                    "event_type": "WITHDRAWAL",
                    "symbol": "ETH",
                    "amount": "1",
                    "occurred_at": "2026-08-20T12:00:00+00:00",
                    "source_label": "MetaMask",
                    "tx_hash": "0xoriginal",
                },
            ),
            price_currencies=(),
        )
        assert event is not None
        # Event.valuations cascades "delete-orphan" — a Valuation must be
        # appended to the parent's collection, not just given a matching
        # event_id, or it gets silently pruned as an orphan on flush.
        event.valuations.append(Valuation(
            quote_currency="EUR", unit_price="2000", total_value="2000",
            requested_timestamp=event.occurred_at, observation_timestamp=event.occurred_at,
            provider="fixture", provider_asset_id="ethereum", method="MANUAL",
        ))
        self.session.commit()
        return event

    def test_adding_a_swap_leg_reclassifies_a_misdetected_withdrawal_as_a_swap(self) -> None:
        event = self._withdrawal()

        result = add_swap_leg(
            event.id,
            AddSwapLegIn(secondary_asset_symbol="usdc", secondary_amount="1800.50"),
            self.session,
        )

        self.assertEqual(result["values"]["event_type"], "SWAP")
        refreshed = self.session.get(Event, event.id)
        self.assertEqual(refreshed.secondary_asset.symbol, "USDC")
        self.assertEqual(refreshed.secondary_amount, "1800.50")

    def test_primary_leg_and_its_existing_valuation_are_untouched(self) -> None:
        event = self._withdrawal()

        add_swap_leg(event.id, AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount="1800.50"), self.session)

        refreshed = self.session.get(Event, event.id)
        self.assertEqual(refreshed.primary_amount, "1")
        self.assertEqual(refreshed.tx_hash, "0xoriginal")
        eur_valuations = [v for v in refreshed.valuations if v.provider == "fixture"]
        self.assertEqual(len(eur_valuations), 1)
        self.assertEqual(eur_valuations[0].total_value, "2000")

    def test_can_add_a_leg_without_forcing_a_reclassification(self) -> None:
        event = self._withdrawal()

        result = add_swap_leg(
            event.id,
            AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount="1800.50", event_type=None),
            self.session,
        )

        self.assertEqual(result["values"]["event_type"], "WITHDRAWAL")

    def test_rejects_a_second_call_once_a_leg_already_exists(self) -> None:
        event = self._withdrawal()
        add_swap_leg(event.id, AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount="1800.50"), self.session)

        with self.assertRaises(HTTPException) as ctx:
            add_swap_leg(event.id, AddSwapLegIn(secondary_asset_symbol="DAI", secondary_amount="5"), self.session)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_a_non_positive_amount(self) -> None:
        event = self._withdrawal()
        for bad_amount in ("0", "-5", "not-a-number"):
            with self.assertRaises(HTTPException):
                add_swap_leg(event.id, AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount=bad_amount), self.session)

    def test_reuses_an_existing_asset_instead_of_duplicating_it(self) -> None:
        first = self._withdrawal("w1")
        second = self._withdrawal("w2")
        add_swap_leg(first.id, AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount="100"), self.session)
        add_swap_leg(second.id, AddSwapLegIn(secondary_asset_symbol="USDC", secondary_amount="200"), self.session)

        refreshed_first = self.session.get(Event, first.id)
        refreshed_second = self.session.get(Event, second.id)
        self.assertEqual(refreshed_first.secondary_asset_id, refreshed_second.secondary_asset_id)


if __name__ == "__main__":
    unittest.main()
