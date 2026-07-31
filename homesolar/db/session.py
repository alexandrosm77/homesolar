from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from homesolar.db.models import Base

SessionLocal = sessionmaker(autocommit=False, autoflush=False)
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = 10_000


def engine_from_url(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    connect_args: dict[str, Any] = (
        {"check_same_thread": False, "timeout": SQLITE_TIMEOUT_SECONDS} if is_sqlite else {}
    )
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if is_sqlite:
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def _apply_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def sessionmaker_from_engine(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
