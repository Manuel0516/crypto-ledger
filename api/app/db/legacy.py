"""Compatibility preparation for the first Dockerized MVP database.

The prototype wrote a SQLite database directly into ``data/ledger.db`` before
Alembic owned the schema.  Docker mounts that directory, so an existing
prototype database must be upgraded before Alembic tries to run the initial
revision.  This module performs only additive, data-preserving changes and
stamps the initial revision once the database matches the ORM contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection, text

HEAD_REVISION = "5567d8494b28"


def _tables(connection: Connection) -> set[str]:
    rows = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _columns(connection: Connection, table: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _add_missing_columns(connection: Connection, table: str, columns: Iterable[tuple[str, str]]) -> None:
    existing = _columns(connection, table)
    for name, definition in columns:
        if name not in existing:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _create_missing_tables(connection: Connection, tables: set[str]) -> None:
    if "fees" not in tables:
        connection.exec_driver_sql(
            """
            CREATE TABLE fees (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                fee_type VARCHAR NOT NULL,
                fee_asset_id INTEGER NOT NULL,
                fee_amount VARCHAR NOT NULL,
                FOREIGN KEY(event_id) REFERENCES events(id),
                FOREIGN KEY(fee_asset_id) REFERENCES assets(id)
            )
            """
        )
    if "price_observations" not in tables:
        connection.exec_driver_sql(
            """
            CREATE TABLE price_observations (
                id INTEGER PRIMARY KEY,
                provider VARCHAR NOT NULL,
                provider_asset_id VARCHAR NOT NULL,
                quote_currency VARCHAR NOT NULL,
                observation_date VARCHAR NOT NULL,
                unit_price VARCHAR NOT NULL,
                method VARCHAR NOT NULL,
                fetched_at DATETIME NOT NULL,
                CONSTRAINT uq_price_observation UNIQUE
                    (provider, provider_asset_id, quote_currency, observation_date)
            )
            """
        )
    if "overrides" not in tables:
        connection.exec_driver_sql(
            """
            CREATE TABLE overrides (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                field VARCHAR NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at DATETIME NOT NULL,
                reason TEXT,
                FOREIGN KEY(event_id) REFERENCES events(id)
            )
            """
        )
    if "backup_records" not in tables:
        connection.exec_driver_sql(
            """
            CREATE TABLE backup_records (
                id INTEGER PRIMARY KEY,
                created_at DATETIME NOT NULL,
                path VARCHAR NOT NULL,
                sha256 VARCHAR NOT NULL,
                size_bytes INTEGER NOT NULL,
                verified BOOLEAN NOT NULL,
                verified_at DATETIME
            )
            """
        )


def _copy_legacy_fees(connection: Connection) -> None:
    event_columns = _columns(connection, "events")
    if not {"fee_asset", "fee_amount"}.issubset(event_columns):
        return

    rows = connection.execute(
        text("SELECT id, fee_asset, fee_amount FROM events WHERE fee_amount IS NOT NULL")
    ).fetchall()
    for row in rows:
        fee_amount = str(row.fee_amount).strip()
        if not fee_amount or fee_amount in {"0", "0.0", "0.00"}:
            continue
        symbol = str(row.fee_asset or "").strip().upper()
        if not symbol:
            continue
        asset_id = connection.execute(
            text("SELECT id FROM assets WHERE upper(symbol) = :symbol ORDER BY id LIMIT 1"),
            {"symbol": symbol},
        ).scalar_one_or_none()
        if asset_id is None:
            connection.execute(
                text(
                    "INSERT INTO assets (symbol, name, asset_type, network, coingecko_id, decimals) "
                    "VALUES (:symbol, :name, 'COIN', NULL, NULL, NULL)"
                ),
                {"symbol": symbol, "name": symbol},
            )
            asset_id = connection.execute(
                text("SELECT id FROM assets WHERE upper(symbol) = :symbol ORDER BY id DESC LIMIT 1"),
                {"symbol": symbol},
            ).scalar_one()
        already_copied = connection.execute(
            text("SELECT 1 FROM fees WHERE event_id = :event_id LIMIT 1"),
            {"event_id": row.id},
        ).scalar_one_or_none()
        if already_copied is None:
            connection.execute(
                text(
                    "INSERT INTO fees (event_id, fee_type, fee_asset_id, fee_amount) "
                    "VALUES (:event_id, 'LEGACY_FEE', :asset_id, :fee_amount)"
                ),
                {"event_id": row.id, "asset_id": asset_id, "fee_amount": fee_amount},
            )


def _stamp_head(connection: Connection) -> None:
    tables = _tables(connection)
    if "alembic_version" not in tables:
        connection.exec_driver_sql(
            """
            CREATE TABLE alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            )
            """
        )
    current = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
    if current is None:
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": HEAD_REVISION},
        )


def prepare_database(connection: Connection) -> None:
    """Make a legacy SQLite database safe for ``alembic upgrade head``.

    Empty databases are left untouched so Alembic can create the schema from
    the normal revision.  Canonical databases are also left untouched.  Only
    databases that have the prototype's ``accounts``/``events`` tables but
    lack canonical tables are adjusted.
    """

    if connection.dialect.name != "sqlite":
        return

    tables = _tables(connection)
    if "accounts" not in tables or "events" not in tables:
        connection.commit()
        return

    version = None
    if "alembic_version" in tables:
        version = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
    if version is not None:
        connection.commit()
        return

    _add_missing_columns(
        connection,
        "accounts",
        [("wallet_software", "VARCHAR"), ("created_at", "DATETIME"), ("archived_at", "DATETIME")],
    )
    connection.exec_driver_sql(
        "UPDATE accounts SET created_at = COALESCE(created_at, last_sync, datetime('now'))"
    )
    _add_missing_columns(connection, "assets", [("contract_address", "VARCHAR")])
    _add_missing_columns(connection, "events", [("updated_at", "DATETIME")])
    connection.exec_driver_sql(
        "UPDATE events SET updated_at = COALESCE(updated_at, created_at, datetime('now'))"
    )
    connection.exec_driver_sql(
        "UPDATE events SET primary_amount = '0', status = 'REQUIRES_REVIEW' "
        "WHERE primary_amount IS NULL OR trim(primary_amount) = ''"
    )
    _add_missing_columns(connection, "issues", [("created_at", "DATETIME")])
    connection.exec_driver_sql("UPDATE issues SET created_at = COALESCE(created_at, datetime('now'))")

    _create_missing_tables(connection, tables)
    _copy_legacy_fees(connection)
    _stamp_head(connection)
    # Alembic will see the stamped head and therefore has no migration
    # transaction of its own to commit these preparatory SQLite DDL changes.
    connection.commit()
