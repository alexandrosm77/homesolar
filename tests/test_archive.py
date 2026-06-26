from datetime import UTC, datetime, timedelta

from homesolar.archive import Archive
from homesolar.db import models
from homesolar.db.session import create_schema, engine_from_url, sessionmaker_from_engine


def test_archive_produced_energy_prefers_highest_daily_counter_for_local_day() -> None:
    archive, session_factory = _archive()
    now = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        _add_inverter(session, "one")
        first = _add_reading(session, "one", now - timedelta(hours=1), today=1.0)
        second = _add_reading(session, "one", now, today=1.25)
        session.flush()
        session.add(
            models.EnergyInterval(
                inverter_id="one",
                start_at=first.observed_at,
                end_at=second.observed_at,
                start_reading_id=first.id,
                end_reading_id=second.id,
                generated_kwh=99,
                source_counter="lifetime",
                confidence="normal",
            )
        )
        session.commit()

    snapshot = archive.dashboard_snapshot(now)

    assert snapshot.total_today_kwh == 1.25
    assert snapshot.inverters[0].produced_energy_today_kwh == 1.25


def test_archive_produced_energy_falls_back_to_normal_confidence_intervals() -> None:
    archive, session_factory = _archive()
    now = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        _add_inverter(session, "one")
        first = _add_reading(session, "one", now - timedelta(hours=2), today=None)
        second = _add_reading(session, "one", now - timedelta(hours=1), today=None)
        third = _add_reading(session, "one", now, today=None)
        session.flush()
        session.add_all(
            [
                _interval("one", first, second, 0.25, "normal"),
                _interval("one", second, third, 7.5, "implausible_delta"),
            ]
        )
        session.commit()

    snapshot = archive.dashboard_snapshot(now)

    assert snapshot.total_today_kwh == 0.25
    assert snapshot.inverters[0].produced_energy_today_kwh == 0.25


def test_archive_dashboard_snapshot_returns_health_components_and_recent_events() -> None:
    archive, session_factory = _archive()
    now = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        _add_inverter(session, "one", name="One", last_seen_at=now)
        _add_inverter(session, "two", name="Two", last_seen_at=now)
        one = _add_reading(session, "one", now - timedelta(minutes=1), power=1500, today=1.25)
        _add_reading(session, "two", now - timedelta(minutes=30), power=500, today=0.5)
        session.flush()
        session.add(
            models.ComponentReading(
                inverter_id="one",
                observed_at=one.observed_at,
                reading_id=one.id,
                component_type="channel",
                component_name="channel_1",
                power_w=650,
            )
        )
        session.add(_poll("one", now, success=True))
        session.add(_poll("two", now, success=False, error="timeout"))
        session.add(
            models.AlarmSnapshot(
                inverter_id="two",
                observed_at=now,
                status="fault",
                alarms={"grid": True},
            )
        )
        session.commit()

    snapshot = archive.dashboard_snapshot(now)

    assert snapshot.total_power_w == 2000
    assert snapshot.total_today_kwh == 1.75
    assert snapshot.online_count == 1
    assert snapshot.alarm_count == 1
    assert snapshot.poll_error_count == 1
    assert [item.inverter.id for item in snapshot.inverters] == ["one", "two"]
    assert snapshot.inverters[0].health.state == "online"
    assert snapshot.inverters[0].components[0].component_name == "channel_1"
    assert snapshot.inverters[1].health.state == "alarm"
    assert snapshot.recent_events[0].inverter_id == "two"


def test_archive_uses_trailing_14_local_days_for_median() -> None:
    archive, session_factory = _archive()
    now = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        _add_inverter(session, "one", name="One")
        _add_inverter(session, "two", name="Two")
        for offset in range(1, 15):
            _add_reading(session, "one", now - timedelta(days=offset), today=float(offset))
            _add_reading(session, "two", now - timedelta(days=offset), today=float(offset * 2))
        _add_reading(session, "one", now, today=100.0)
        _add_reading(session, "two", now, today=100.0)
        session.commit()

    assert archive.median_daily_kwh("one", now=now) == 7.5
    assert archive.median_daily_kwh(now=now) == 22.5


def test_archive_today_power_chart_returns_local_day_power_series() -> None:
    archive, session_factory = _archive()
    now = datetime(2026, 6, 26, 12, 0, tzinfo=UTC)

    with session_factory() as session:
        _add_inverter(session, "one", name="One")
        _add_reading(session, "one", now - timedelta(days=1), power=999, today=1.0)
        _add_reading(session, "one", now - timedelta(minutes=10), power=1000, today=1.0)
        _add_reading(session, "one", now, power=1500, today=1.25)
        _add_reading(session, "one", now + timedelta(minutes=5), power=None, today=1.25)
        session.commit()

    chart = archive.power_chart_today("one", now=now)

    assert chart.range_name == "today"
    assert chart.series[0].inverter_id == "one"
    assert chart.series[0].name == "One"
    assert [point.power_w for point in chart.series[0].points] == [1000, 1500]


def _archive():
    engine = engine_from_url("sqlite:///:memory:")
    create_schema(engine)
    session_factory = sessionmaker_from_engine(engine)
    return Archive(session_factory), session_factory


def _add_inverter(session, inverter_id: str, name: str | None = None, last_seen_at=None) -> None:
    session.add(
        models.Inverter(
            id=inverter_id,
            name=name or inverter_id.title(),
            type="kostal_html",
            base_url="http://example.test",
            timezone="Europe/London",
            enabled=True,
            last_seen_at=last_seen_at,
        )
    )


def _add_reading(
    session,
    inverter_id: str,
    observed_at: datetime,
    power: float | None = 1000,
    today: float | None = 1.0,
) -> models.Reading:
    reading = models.Reading(
        inverter_id=inverter_id,
        observed_at=observed_at,
        current_power_w=power,
        energy_today_kwh=today,
        energy_lifetime_kwh=None,
        energy_session_kwh=None,
        status="ok",
        extra={},
    )
    session.add(reading)
    return reading


def _interval(
    inverter_id: str,
    first: models.Reading,
    second: models.Reading,
    generated_kwh: float,
    confidence: str,
) -> models.EnergyInterval:
    return models.EnergyInterval(
        inverter_id=inverter_id,
        start_at=first.observed_at,
        end_at=second.observed_at,
        start_reading_id=first.id,
        end_reading_id=second.id,
        generated_kwh=generated_kwh,
        source_counter="lifetime",
        confidence=confidence,
    )


def _poll(
    inverter_id: str, observed_at: datetime, success: bool, error: str | None = None
) -> models.PollEvent:
    return models.PollEvent(
        inverter_id=inverter_id,
        kind="live",
        started_at=observed_at,
        finished_at=observed_at + timedelta(milliseconds=100),
        duration_ms=100,
        success=success,
        error=error,
    )