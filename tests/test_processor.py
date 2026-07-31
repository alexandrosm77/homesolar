import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from homesolar.adapters.base import AdapterResult, NormalizedReading, RawContentType, RawPayload
from homesolar.collector.processor import ensure_inverter, store_adapter_result
from homesolar.collector.scheduler import CollectorService
from homesolar.config import AppConfig, InverterConfig
from homesolar.db import models
from homesolar.db.session import (
    SQLITE_BUSY_TIMEOUT_MS,
    create_schema,
    engine_from_url,
    sessionmaker_from_engine,
)


def test_processor_stores_interval_from_lifetime_counter() -> None:
    engine = engine_from_url("sqlite:///:memory:")
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    config = InverterConfig(
        id="test",
        name="Test",
        type="kostal_html",
        base_url="http://example.test",
    )
    first_at = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)

    with session_factory() as session:
        ensure_inverter(session, config)
        store_adapter_result(session, config, _result(first_at, 100.0))
        store_adapter_result(session, config, _result(first_at + timedelta(minutes=1), 100.05))
        session.commit()

        inverter = session.get(models.Inverter, "test")
        interval = session.scalars(select(models.EnergyInterval)).one()
        assert inverter is not None
        assert inverter.first_seen_at.replace(tzinfo=UTC) == first_at
        assert inverter.last_seen_at.replace(tzinfo=UTC) == first_at + timedelta(minutes=1)
        assert interval.generated_kwh == 0.05
        assert interval.source_counter == "lifetime"
        assert interval.confidence == "normal"


def test_processor_prefers_daily_counter_for_interval_delta() -> None:
    engine = engine_from_url("sqlite:///:memory:")
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    config = InverterConfig(
        id="test",
        name="Test",
        type="apsystems_ez1d",
        base_url="http://example.test",
    )
    first_at = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)

    with session_factory() as session:
        ensure_inverter(session, config)
        store_adapter_result(
            session,
            config,
            _result(first_at, lifetime=100.0, today=1.0, power_w=1000),
        )
        store_adapter_result(
            session,
            config,
            _result(first_at + timedelta(minutes=1), lifetime=110.0, today=1.05, power_w=1000),
        )
        session.commit()

        interval = session.scalars(select(models.EnergyInterval)).one()
        assert interval.generated_kwh == 0.05
        assert interval.source_counter == "daily"
        assert interval.confidence == "normal"


def test_processor_rejects_implausible_counter_jump() -> None:
    engine = engine_from_url("sqlite:///:memory:")
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    config = InverterConfig(
        id="test",
        name="Test",
        type="apsystems_ez1d",
        base_url="http://example.test",
    )
    first_at = datetime(2026, 5, 25, 10, 0, tzinfo=UTC)

    with session_factory() as session:
        ensure_inverter(session, config)
        store_adapter_result(session, config, _result(first_at, lifetime=0.0, power_w=0))
        store_adapter_result(
            session,
            config,
            _result(first_at + timedelta(minutes=1), lifetime=7.5, power_w=530),
        )
        session.commit()

        interval = session.scalars(select(models.EnergyInterval)).one()
        assert interval.generated_kwh is None
        assert interval.source_counter == "lifetime"
        assert interval.confidence == "implausible_delta"
        assert "exceeds plausible" in (interval.notes or "")


def test_processor_rejects_counter_delta_across_local_days() -> None:
    engine = engine_from_url("sqlite:///:memory:")
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    config = InverterConfig(
        id="test",
        name="Test",
        type="kostal_html",
        base_url="http://example.test",
        timezone="Europe/Athens",
    )
    first_at = datetime(2026, 5, 25, 20, 59, tzinfo=UTC)

    with session_factory() as session:
        ensure_inverter(session, config)
        store_adapter_result(session, config, _result(first_at, lifetime=100.0, today=5.0))
        store_adapter_result(
            session,
            config,
            _result(first_at + timedelta(minutes=2), lifetime=100.2, today=5.2),
        )
        session.commit()

        interval = session.scalars(select(models.EnergyInterval)).one()
        assert interval.generated_kwh is None
        assert interval.source_counter == "daily"
        assert interval.confidence == "cross_day_counter"
        assert "local day boundary" in (interval.notes or "")


def test_sqlite_engine_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    engine = engine_from_url(f"sqlite:///{tmp_path / 'homesolar.sqlite'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == SQLITE_BUSY_TIMEOUT_MS


def test_poll_loop_survives_database_errors() -> None:
    config = InverterConfig(
        id="test",
        name="Test",
        type="apsystems_ez1d",
        base_url="http://example.test",
    )

    def locked_session_factory() -> None:
        raise OperationalError("INSERT", {}, Exception("database is locked"))

    async def scenario() -> int:
        service = CollectorService(AppConfig(inverters=[config]), locked_session_factory)
        service._locks[config.id] = asyncio.Lock()
        polls = 0

        async def fetch() -> AdapterResult | None:
            nonlocal polls
            polls += 1
            if polls == 3:
                service._stopped.set()
            return _result(datetime.now(UTC), lifetime=100.0)

        await asyncio.wait_for(service._poll_loop(config, "live", 0, fetch), timeout=5)
        return polls

    assert asyncio.run(scenario()) == 3


def test_watchdog_reports_inverter_whose_live_loop_stopped(caplog) -> None:
    config = InverterConfig(
        id="apsystems",
        name="APsystems",
        type="apsystems_ez1d",
        base_url="http://example.test",
    )
    now = datetime.now(UTC)
    service = CollectorService(AppConfig(inverters=[config]), sessionmaker())
    service._last_live_poll_at[config.id] = now - timedelta(hours=27)

    with caplog.at_level(logging.ERROR, logger="homesolar.collector.scheduler"):
        overdue = service.overdue_live_polls(now)

    assert overdue == ["apsystems"]
    assert "is not running" in caplog.text

    service._last_live_poll_at[config.id] = now - timedelta(minutes=1)

    assert service.overdue_live_polls(now) == []


def _result(
    observed_at: datetime,
    lifetime: float,
    today: float | None = None,
    power_w: float = 1000,
) -> AdapterResult:
    return AdapterResult(
        raw=RawPayload(kind="live", content_type=RawContentType.JSON, body="{}"),
        reading=NormalizedReading(
            observed_at=observed_at,
            current_power_w=power_w,
            energy_today_kwh=today,
            energy_lifetime_kwh=lifetime,
        ),
    )
