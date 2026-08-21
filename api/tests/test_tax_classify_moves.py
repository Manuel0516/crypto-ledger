from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ledger.overrides import effective_values
from app.core.tax.common import EffectiveEvent, build_supplementary_rows, classify_moves
from app.db.models import Account, Asset, Base, Event, EventLink

OCCURRED_AT = datetime(2026, 7, 22, tzinfo=timezone.utc)


class ClassifyMovesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.account = Account(name="Bitget", kind="exchange", connector_type="bitget_live")
        self.trx = Asset(symbol="TRX", name="TRON", asset_type="COIN", network="TRON")
        self.session.add_all([self.account, self.trx])
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _event(self, **kwargs) -> EffectiveEvent:
        defaults = dict(
            external_id=f"evt-{kwargs.get('event_type', 'X')}-{id(kwargs)}",
            account_id=self.account.id,
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=self.trx.id,
            source_label="Bitget",
            provenance="automatic",
            normalizer_version="test",
        )
        defaults.update(kwargs)
        event = Event(**defaults)
        self.session.add(event)
        self.session.flush()
        return EffectiveEvent(event, effective_values(self.session, event)[0])

    def test_self_canceling_internal_transfer_is_never_ambiguous(self) -> None:
        # Bitget's own uta_internal_transfer shape: one event, a secondary
        # leg of the same asset/amount that cancels the primary leg — no
        # separate counterpart event exists to link to, so this must not
        # block tax readiness the way a real, unresolved deposit would.
        event = self._event(
            event_type="TRANSFER",
            direction="+",
            primary_amount="0.9",
            secondary_asset_id=self.trx.id,
            secondary_amount="0.9",
        )
        pairs, ambiguous = classify_moves([event])
        self.assertEqual(pairs, [])
        self.assertEqual(ambiguous, [])

    def test_a_genuine_unlinked_deposit_is_still_flagged_ambiguous(self) -> None:
        event = self._event(event_type="DEPOSIT", direction="+", primary_amount="0.9")
        pairs, ambiguous = classify_moves([event])
        self.assertEqual(pairs, [])
        self.assertEqual([e.id for e in ambiguous], [event._event.id])

    def test_explicitly_internal_unlinked_move_is_not_ambiguous(self) -> None:
        event = self._event(
            event_type="WITHDRAWAL",
            direction="-",
            primary_amount="0.9",
            internal_transfer=True,
            destination_label="My hardware wallet",
        )
        pairs, ambiguous = classify_moves([event])
        self.assertEqual(pairs, [])
        self.assertEqual(ambiguous, [])

    def test_explicitly_internal_move_is_listed_as_a_standalone_transfer(self) -> None:
        event = self._event(
            event_type="WITHDRAWAL",
            direction="-",
            primary_amount="0.9",
            internal_transfer=True,
            destination_label="My hardware wallet",
        )
        rows, _corrections, _schedule, _total, _reconciliation = build_supplementary_rows(
            self.session, [event], [], OCCURRED_AT.year
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].event_ids, [event.id])
        self.assertEqual(rows[0].to_label, "My hardware wallet")

    def test_a_secondary_leg_of_a_different_asset_is_not_treated_as_self_canceling(self) -> None:
        # A real trade's quote leg (e.g. BUY BTC / SELL USDT) must still be
        # flagged if event_type happens to be a move type — only a literal
        # same-asset, same-amount secondary leg is exempted.
        usdt = Asset(symbol="USDT", name="Tether", asset_type="STABLECOIN")
        self.session.add(usdt)
        self.session.flush()
        event = self._event(
            event_type="TRANSFER",
            direction="+",
            primary_amount="0.9",
            secondary_asset_id=usdt.id,
            secondary_amount="5",
        )
        _pairs, ambiguous = classify_moves([event])
        self.assertEqual([e.id for e in ambiguous], [event._event.id])

    def test_a_properly_linked_withdrawal_deposit_pair_still_resolves(self) -> None:
        withdrawal = self._event(event_type="WITHDRAWAL", direction="-", primary_amount="1.0")
        deposit = self._event(event_type="DEPOSIT", direction="+", primary_amount="1.0")
        self.session.add(
            EventLink(event_id=withdrawal._event.id, linked_event_id=deposit._event.id, relationship_type="INTERNAL_TRANSFER")
        )
        self.session.flush()
        self.session.refresh(withdrawal._event)
        self.session.refresh(deposit._event)
        withdrawal = EffectiveEvent(withdrawal._event, effective_values(self.session, withdrawal._event)[0])
        deposit = EffectiveEvent(deposit._event, effective_values(self.session, deposit._event)[0])

        pairs, ambiguous = classify_moves([withdrawal, deposit])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(ambiguous, [])


if __name__ == "__main__":
    unittest.main()
