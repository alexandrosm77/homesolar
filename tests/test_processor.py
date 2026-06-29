from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from homesolar.adapters.base import AdapterResult, NormalizedReading, RawContentType, RawPayload
from homesolar.collector.processor import ensure_inverter, store_adapter_result
from homesolar.config import InverterConfig
from homesolar.db import models
from homesolar.db.session import create_schema, engine_from_url, sessionmaker_from_engine


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
