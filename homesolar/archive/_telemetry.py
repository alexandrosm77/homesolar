from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._energy import produced_energy_today
from homesolar.archive._queries import (
    components_for_readings,
    energy_interval_snapshot,
    inverter_identity,
    latest_alarms,
    latest_poll_events,
    latest_readings,
    poll_event_snapshot,
    reading_snapshot,
    recent_poll_events,
)
from homesolar.archive._time import as_utc
from homesolar.archive.read_model import (
    AlarmSnapshot,
    DashboardSnapshot,
    EnergyIntervalSnapshot,
    InverterSnapshot,
    PollEventSnapshot,
    ReadingSnapshot,
    TelemetryHealth,
)
from homesolar.db import models

ONLINE_READING_MAX_AGE_SECONDS = 900


def dashboard_snapshot(session: Session, now: datetime | None = None) -> DashboardSnapshot:
    now = as_utc(now or datetime.now(UTC))
    inverters = session.scalars(select(models.Inverter).order_by(models.Inverter.name)).all()
    inverter_ids = [inverter.id for inverter in inverters]
    latest_by_inverter = latest_readings(session, inverter_ids)
    polls_by_inverter = latest_poll_events(session, inverter_ids)
    alarms_by_inverter = latest_alarms(session, inverter_ids)
    components_by_reading = components_for_readings(
        session, [reading.id for reading in latest_by_inverter.values()]
    )

    items: list[InverterSnapshot] = []
    total_power = 0.0
    total_today = 0.0
    online_count = 0
    alarm_count = 0
    poll_error_count = 0

    for inverter in inverters:
        latest = latest_by_inverter.get(inverter.id)
        last_poll = polls_by_inverter.get(inverter.id)
        alarm = alarms_by_inverter.get(inverter.id)
        today = produced_energy_today(session, inverter, now)
        latest_power = latest.current_power_w if latest else None
        health = telemetry_health(inverter, latest, last_poll, alarm, now)

        if latest_power is not None:
            total_power += latest_power
        if today is not None:
            total_today += today
        if health.is_online:
            online_count += 1
        if alarm and alarm.status != "normal":
            alarm_count += 1
        if last_poll and not last_poll.success:
            poll_error_count += 1

        items.append(
            InverterSnapshot(
                inverter=inverter_identity(inverter),
                latest=latest,
                produced_energy_today_kwh=today,
                last_poll=last_poll,
                latest_alarm=alarm,
                components=components_by_reading.get(latest.id, []) if latest else [],
                health=health,
            )
        )

    return DashboardSnapshot(
        total_power_w=total_power,
        total_today_kwh=round(total_today, 3),
        inverters=items,
        online_count=online_count,
        alarm_count=alarm_count,
        poll_error_count=poll_error_count,
        updated_at=now,
        recent_events=recent_poll_events(session, 6),
    )


def readings(
    session: Session,
    inverter_id: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 500,
) -> list[ReadingSnapshot]:
    stmt = select(models.Reading).order_by(models.Reading.observed_at.desc()).limit(limit)
    if inverter_id:
        stmt = stmt.where(models.Reading.inverter_id == inverter_id)
    if from_:
        stmt = stmt.where(models.Reading.observed_at >= from_)
    if to:
        stmt = stmt.where(models.Reading.observed_at <= to)
    rows = session.scalars(stmt).all()
    return [reading_snapshot(row) for row in reversed(rows)]


def energy_intervals(
    session: Session,
    inverter_id: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 1000,
) -> list[EnergyIntervalSnapshot]:
    stmt = select(models.EnergyInterval).order_by(models.EnergyInterval.start_at.desc()).limit(limit)
    if inverter_id:
        stmt = stmt.where(models.EnergyInterval.inverter_id == inverter_id)
    if from_:
        stmt = stmt.where(models.EnergyInterval.end_at >= from_)
    if to:
        stmt = stmt.where(models.EnergyInterval.start_at <= to)
    rows = session.scalars(stmt).all()
    return [energy_interval_snapshot(row) for row in reversed(rows)]


def poll_events(
    session: Session, inverter_id: str | None = None, limit: int = 200
) -> list[PollEventSnapshot]:
    stmt = select(models.PollEvent).order_by(models.PollEvent.started_at.desc()).limit(limit)
    if inverter_id:
        stmt = stmt.where(models.PollEvent.inverter_id == inverter_id)
    rows = session.scalars(stmt).all()
    return [poll_event_snapshot(row) for row in rows]


def telemetry_health(
    inverter: models.Inverter,
    latest: ReadingSnapshot | None,
    last_poll: PollEventSnapshot | None,
    alarm: AlarmSnapshot | None,
    now: datetime,
) -> TelemetryHealth:
    age_seconds = _age_seconds(latest.observed_at, now) if latest else None
    seen_age_seconds = _age_seconds(inverter.last_seen_at, now) if inverter.last_seen_at else None
    is_online = bool(
        latest
        and last_poll
        and last_poll.success
        and (age_seconds is None or age_seconds < ONLINE_READING_MAX_AGE_SECONDS)
    )
    if alarm and alarm.status != "normal":
        state = "alarm"
    elif last_poll and not last_poll.success:
        state = "poll_error"
    elif is_online:
        state = "online"
    else:
        state = "waiting"
    return TelemetryHealth(is_online, state, age_seconds, seen_age_seconds)


def _age_seconds(value: datetime, now: datetime) -> int:
    return max(0, int((as_utc(now) - as_utc(value)).total_seconds()))