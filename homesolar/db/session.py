from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from homesolar.db.models import Base

SessionLocal = sessionmaker(autocommit=False, autoflush=False)


def engine_from_url(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def sessionmaker_from_engine(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
