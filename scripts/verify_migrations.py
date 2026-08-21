#!/usr/bin/env python3
"""Upgrade a representative pre-head ledger fixture in an isolated database."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


INITIAL_REVISION = "5567d8494b28"


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    api_directory = repository / "api"
    alembic = api_directory / ".venv/bin/alembic"

    with tempfile.TemporaryDirectory(prefix="crypto-ledger-migration-") as temporary:
        database_path = Path(temporary) / "representative.db"
        environment = {**os.environ, "DATABASE_URL": f"sqlite:///{database_path}"}

        subprocess.run([str(alembic), "upgrade", INITIAL_REVISION], cwd=api_directory, env=environment, check=True)

        payload = {"type": "deposit", "coin": "BTC", "amount": "1.25"}
        payload_json = json.dumps(payload, sort_keys=True)
        timestamp = datetime(2026, 8, 20, tzinfo=timezone.utc).isoformat()
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                "INSERT INTO accounts (name, kind, status, wallet_software, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("Representative exchange", "exchange", "connected", None, "migration fixture", timestamp),
            )
            connection.execute(
                "INSERT INTO assets (symbol, name, asset_type, network, coingecko_id, decimals) VALUES (?, ?, ?, ?, ?, ?)",
                ("BTC", "Bitcoin", "COIN", None, "bitcoin", 8),
            )
            connection.execute(
                "INSERT INTO raw_events (source_id, external_id, received_at, source_timestamp, payload_json, payload_hash, connector_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("fixture", "fixture-raw-1", timestamp, timestamp, payload_json, hashlib.sha256(payload_json.encode()).hexdigest(), "fixture-1"),
            )
            connection.execute(
                "INSERT INTO events (external_id, raw_event_id, account_id, event_type, direction, status, occurred_at, original_timestamp, primary_asset_id, primary_amount, source_label, destination_label, provenance, normalizer_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("fixture-event-1", 1, None, "DEPOSIT", "+", "COMPLETE", timestamp, timestamp, 1, "1.25", "Legacy source", "Representative destination", "automatic", "fixture-1", timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO valuations (event_id, quote_currency, unit_price, total_value, requested_timestamp, observation_timestamp, provider, provider_asset_id, method, confidence, manual_override) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "EUR", "50000", "62500", timestamp, timestamp, "fixture", "bitcoin", "DAILY_REFERENCE", "high", 0),
            )
            connection.execute(
                "INSERT INTO price_observations (provider, provider_asset_id, quote_currency, observation_date, unit_price, method, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("fixture", "bitcoin", "EUR", "2026-08-20", "50000", "DAILY_REFERENCE", timestamp),
            )
            connection.commit()
        finally:
            connection.close()

        subprocess.run([str(alembic), "upgrade", "head"], cwd=api_directory, env=environment, check=True)

        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM valuations").fetchone()[0] == 1
            assert connection.execute("SELECT address_from, address_to FROM events WHERE id = 1").fetchone() == ("Legacy source", "Representative destination")
            account_columns = {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}
            event_columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
            valuation_columns = {row[1] for row in connection.execute("PRAGMA table_info(valuations)")}
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert "connector_type" in account_columns
            for removed_column in ("source_label", "description", "merchant", "tags_json", "evidence_reference", "notes"):
                assert removed_column not in event_columns
            assert "counterparty" not in event_columns
            assert "destination_label" not in event_columns
            assert "granularity" in valuation_columns
            assert "account_balances" in tables
            settings_columns = {row[1] for row in connection.execute("PRAGMA table_info(app_settings)")}
            assert "explorer_api_keys_encrypted" in settings_columns
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c9d0e1f2a3b4"
        finally:
            connection.close()

    print("Representative migration check passed.")


if __name__ == "__main__":
    main()
