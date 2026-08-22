#!/usr/bin/env python3
"""Exercise the release-critical recovery and import flows in a temp database.

This deliberately never reads the repository's configured database or backup
directory. It is safe to run before publishing a release or after building a
deployment image.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _Upload:
    def __init__(self, content: bytes):
        self.content = content

    async def read(self) -> bytes:
        return self.content


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    api_directory = repository / "api"
    sys.path.insert(0, str(api_directory))

    with tempfile.TemporaryDirectory(prefix="crypto-ledger-release-") as temporary:
        root = Path(temporary)
        database_path = root / "ledger.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
        os.environ["BACKUP_DIR"] = str(root / "backups")
        os.environ["BACKUP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

        from app.api.imports import import_bitget
        from app.connectors.base import RawRecord
        from app.connectors.manual import ManualConnector
        from app.core.backup.service import backup_bytes, create_backup, import_backup, restore_backup, verify_backup
        from app.core.ledger.service import ingest
        from app.db.models import Account, Base

        engine = create_engine(os.environ["DATABASE_URL"])
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            session.add(Account(name="Release fixture", kind="manual", connector_type="manual"))
            session.commit()

            backup = create_backup(session)
            session.commit()
            verify_backup(session, backup.id)
            session.commit()
            _, ciphertext = backup_bytes(session, backup.id)
            uploaded = import_backup(session, ciphertext)
            session.commit()
            assert uploaded.verified, "uploaded backup was not marked verified"

            session.add(Account(name="Should disappear after restore", kind="manual", connector_type="manual"))
            session.commit()
            restore_backup(session, backup.id)
            session.expire_all()
            assert session.query(Account).filter_by(name="Should disappear after restore").count() == 0

            manual = ManualConnector()
            ingest(
                session,
                manual,
                RawRecord(
                    "manual",
                    "release-evidence-1",
                    datetime(2026, 8, 21, tzinfo=timezone.utc),
                    {
                        "event_type": "DEPOSIT",
                        "symbol": "BTC",
                        "amount": "1",
                        "occurred_at": "2026-08-21T00:00:00+00:00",
                        "source_label": "Release fixture",
                    },
                ),
                price_currencies=(),
            )
            session.commit()

            bitget_result = asyncio.run(
                import_bitget(
                    session,
                    _Upload(
                        b'[{"id":"release-bitget-1","type":"deposit","timestamp":"2026-08-21T00:00:00+00:00","coin":"BTC","amount":"0.5"}]'
                    ),
                )
            )
            assert bitget_result["imported"] == 1, "Bitget import did not create one event"
        finally:
            session.close()
            engine.dispose()

    print("Release recovery and import checks passed.")


if __name__ == "__main__":
    main()
