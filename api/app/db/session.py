from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ledger.db")
if DB_URL.startswith("sqlite:///./"):
    Path(DB_URL.removeprefix("sqlite:///./")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DB_URL,
    # A busy connector sync (retrying/backing off against a slow external
    # API) can hold a write for a while; without a generous busy_timeout,
    # any other request touching the DB at that moment gets a hard
    # "database is locked" error instead of just waiting its turn.
    connect_args={"check_same_thread": False, "timeout": 30} if DB_URL.startswith("sqlite") else {},
)

if DB_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        # WAL lets readers keep working while a writer is mid-transaction —
        # the default rollback-journal mode serializes far more aggressively
        # and is the main reason a single slow sync could lock everyone else
        # out (plan §5 commits to SQLite; this is what makes that hold up
        # under concurrent connector syncs + normal UI traffic).
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
