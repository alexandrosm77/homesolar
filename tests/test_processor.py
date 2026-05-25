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

        interval = session.scalars(select(models.EnergyInterval)).one()
        assert interval.generated_kwh == 0.05
        assert interval.source_counter == "lifetime"
        assert interval.confidence == "normal"


def _result(observed_at: datetime, lifetime: float) -> AdapterResult:
    return AdapterResult(
        raw=RawPayload(kind="live", content_type=RawContentType.JSON, body="{}"),
        reading=NormalizedReading(
            observed_at=observed_at,
            current_power_w=1000,
            energy_lifetime_kwh=lifetime,
        ),
    )
