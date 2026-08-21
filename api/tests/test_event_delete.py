from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.events import delete_event
from app.db.models import Attachment, Asset, Base, Event, EventLink, Fee, Issue, Override, RawEvent, Valuation


OCCURRED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


class EventDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_delete_removes_event_owned_data_and_raw_evidence(self) -> None:
        asset = Asset(symbol="BTC", name="Bitcoin", asset_type="COIN", network="Bitcoin")
        self.session.add(asset)
        self.session.flush()

        raw = RawEvent(
            source_id="test",
            external_id="raw-1",
            source_timestamp=OCCURRED_AT,
            payload_json='{"amount": "1"}',
            payload_hash="hash",
            connector_version="test",
        )
        event = Event(
            external_id="event-1",
            raw_event=raw,
            event_type="RECEIVE",
            direction="+",
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=asset.id,
            primary_amount="1",
            address_from="Test source",
            provenance="automatic",
            normalizer_version="test",
        )
        other = Event(
            external_id="event-2",
            event_type="SEND",
            direction="-",
            status="COMPLETE",
            occurred_at=OCCURRED_AT,
            primary_asset_id=asset.id,
            primary_amount="0.5",
            address_from="Test source",
            provenance="manual",
            normalizer_version="test",
        )
        self.session.add_all([event, other])
        self.session.flush()

        with tempfile.TemporaryDirectory() as directory:
            attachment_path = Path(directory) / "evidence.enc"
            attachment_path.write_bytes(b"encrypted evidence")
            self.session.add_all(
                [
                    Fee(event_id=event.id, fee_asset_id=asset.id, fee_amount="0.001"),
                    Valuation(
                        event_id=event.id,
                        quote_currency="EUR",
                        unit_price="100",
                        total_value="100",
                        requested_timestamp=OCCURRED_AT,
                        observation_timestamp=OCCURRED_AT,
                        provider="test",
                        provider_asset_id="btc",
                        method="DAILY_REFERENCE",
                    ),
                    Override(event_id=event.id, field="description", old_value=None, new_value="corrected"),
                    Issue(event_id=event.id, title="Test issue", detail="Needs review"),
                    EventLink(event_id=event.id, linked_event_id=other.id, relationship_type="RELATED"),
                    Attachment(
                        event_id=event.id,
                        kind="other",
                        filename="evidence.txt",
                        content_type="text/plain",
                        size_bytes=18,
                        sha256="hash",
                        storage_path=str(attachment_path),
                    ),
                ]
            )
            self.session.commit()

            result = delete_event(event.id, self.session)

            self.assertEqual(result, {"deleted": event.id})
            self.assertIsNone(self.session.get(Event, event.id))
            self.assertIsNotNone(self.session.get(Event, other.id))
            self.assertIsNone(self.session.get(RawEvent, raw.id))
            self.assertEqual(self.session.query(Fee).count(), 0)
            self.assertEqual(self.session.query(Valuation).count(), 0)
            self.assertEqual(self.session.query(Override).count(), 0)
            self.assertEqual(self.session.query(Issue).count(), 0)
            self.assertEqual(self.session.query(EventLink).count(), 0)
            self.assertEqual(self.session.query(Attachment).count(), 0)
            self.assertFalse(attachment_path.exists())


if __name__ == "__main__":
    unittest.main()
