from __future__ import annotations

import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app.api.events import EventLinkIn, OverrideIn, RestoreIn, create_event_link, get_event, list_events, override_event, restore_event_value
from app.connectors.base import RawRecord
from app.connectors.manual import ManualConnector
from app.core.ledger.service import ingest
from app.core.reporting.evidence import export_evidence_archive, verify_evidence_archive
from app.db.models import Base


class ActivityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.connector = ManualConnector()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _event(self, external_id: str, *, event_type: str = "DEPOSIT", amount: str = "1"):
        event = ingest(
            self.session,
            self.connector,
            RawRecord(
                "manual",
                external_id,
                datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
                {
                    "event_type": event_type,
                    "symbol": "BTC",
                    "amount": amount,
                    "occurred_at": "2026-08-20T12:00:00+00:00",
                    "source_label": "Manual wallet",
                    "description": "Invoice settlement",
                    "merchant": "Example merchant",
                    "tags": ["work", "2026"],
                    "evidence_reference": "receipt-42",
                    "source_timezone": "Europe/Stockholm",
                },
                source_timezone="Europe/Stockholm",
                source_reference="receipt-42",
            ),
            price_currencies=(),
        )
        assert event is not None
        self.session.commit()
        return event

    def test_activity_query_detail_and_restore_keep_evidence_immutable(self) -> None:
        event = self._event("event-1", event_type="unmapped source event")
        page = list_events(
            cursor=None, limit=10, date_from=None, date_to=None, asset="BTC", account_id=None,
            source=None, event_type=None, network=None, provenance=None, internal=None, resolved=None,
            search="merchant", session=self.session,
        )
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["event_type"], "UNKNOWN")
        self.assertNotIn("tags", page["items"][0])

        before_payload = event.raw_event.payload_json
        with self.assertRaises(HTTPException) as error:
            override_event(event.id, OverrideIn(field="description", value="Corrected text", reason="Typo"), self.session)
        self.assertEqual(error.exception.status_code, 400)
        detail = get_event(event.id, self.session)
        self.assertEqual(detail["raw"]["payload"]["description"], "Invoice settlement")
        self.assertEqual(before_payload, event.raw_event.payload_json)

    def test_links_and_integrity_archive_are_auditable(self) -> None:
        first = self._event("event-1", event_type="WITHDRAWAL", amount="-1")
        second = self._event("event-2", event_type="DEPOSIT", amount="1")
        create_event_link(first.id, EventLinkIn(linked_event_id=second.id, relationship_type="INTERNAL_TRANSFER"), self.session)
        detail = get_event(second.id, self.session)
        self.assertEqual(detail["links"][0]["event_id"], first.id)
        self.assertEqual(detail["links"][0]["relationship_type"], "INTERNAL_TRANSFER")

        archive = export_evidence_archive(self.session)
        self.assertTrue(verify_evidence_archive(archive)["valid"])
        with ZipFile(io.BytesIO(archive)) as zip_file:
            self.assertIn("integrity/hashes.csv", zip_file.namelist())
            self.assertIn("integrity/manifest.json", zip_file.namelist())


if __name__ == "__main__":
    unittest.main()
