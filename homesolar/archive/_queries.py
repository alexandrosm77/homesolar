from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from homesolar.archive.read_model import (
    AlarmSnapshot,
    ComponentSnapshot,
    EnergyIntervalSnapshot,
    InverterIdentity,
    PollEventSnapshot,
    ReadingSnapshot,
)
from homesolar.db import models


def filtered_inverters(session: Session, inverter_id: str | None) -> list[models.Inverter]:
    stmt = select(models.Inverter).order_by(models.Inverter.name)
    if inverter_id:
        stmt = stmt.where(models.Inverter.id == inverter_id)
    return list(session.scalars(stmt).all())


def latest_readings(session: Session, inverter_ids: list[str]) -> dict[str, ReadingSnapshot]:
    if not inverter_ids:
        return {}
    ranked = _ranked_ids(models.Reading, models.Reading.observed_at, inverter_ids)
    rows = session.scalars(
        select(models.Reading).join(ranked, models.Reading.id == ranked.c.id).where(ranked.c.rank == 1)
    ).all()
    return {row.inverter_id: reading_snapshot(row) for row in rows}


def latest_poll_events(
    session: Session, inverter_ids: list[str], kind: str | None = None
) -> dict[str, PollEventSnapshot]:
    if not inverter_ids:
        return {}
    ranked = _ranked_ids(
        models.PollEvent,
        models.PollEvent.started_at,
        inverter_ids,
        None if kind is None else models.PollEvent.kind == kind,
    )
    rows = session.scalars(
        select(models.PollEvent).join(ranked, models.PollEvent.id == ranked.c.id).where(ranked.c.rank == 1)
    ).all()
    return {row.inverter_id: poll_event_snapshot(row) for row in rows}


def latest_alarms(session: Session, inverter_ids: list[str]) -> dict[str, AlarmSnapshot]:
    if not inverter_ids:
        return {}
    ranked = _ranked_ids(models.AlarmSnapshot, models.AlarmSnapshot.observed_at, inverter_ids)
    rows = session.scalars(
        select(models.AlarmSnapshot)
        .join(ranked, models.AlarmSnapshot.id == ranked.c.id)
        .where(ranked.c.rank == 1)
    ).all()
    return {row.inverter_id: alarm_snapshot(row) for row in rows}


def components_for_readings(session: Session, reading_ids: list[int]) -> dict[int, list[ComponentSnapshot]]:
    if not reading_ids:
        return {}
    rows = session.scalars(
        select(models.ComponentReading)
        .where(models.ComponentReading.reading_id.in_(reading_ids))
        .order_by(models.ComponentReading.component_type, models.ComponentReading.component_name)
    ).all()
    grouped: dict[int, list[ComponentSnapshot]] = defaultdict(list)
    for row in rows:
        if row.reading_id is not None:
            grouped[row.reading_id].append(component_snapshot(row))
    return dict(grouped)


def recent_poll_events(session: Session, limit: int) -> list[PollEventSnapshot]:
    rows = session.scalars(select(models.PollEvent).order_by(models.PollEvent.started_at.desc()).limit(limit)).all()
    return [poll_event_snapshot(row) for row in rows]


def inverter_identity(inverter: models.Inverter) -> InverterIdentity:
    return InverterIdentity(
        id=inverter.id,
        name=inverter.name,
        type=inverter.type,
        base_url=inverter.base_url,
        enabled=inverter.enabled,
        timezone=inverter.timezone,
        first_seen_at=inverter.first_seen_at,
        last_seen_at=inverter.last_seen_at,
    )


def reading_snapshot(reading: models.Reading) -> ReadingSnapshot:
    return ReadingSnapshot(
        id=reading.id,
        inverter_id=reading.inverter_id,
        observed_at=reading.observed_at,
        current_power_w=reading.current_power_w,
        energy_today_kwh=reading.energy_today_kwh,
        energy_lifetime_kwh=reading.energy_lifetime_kwh,
        energy_session_kwh=reading.energy_session_kwh,
        status=reading.status,
        extra=reading.extra,
    )


def component_snapshot(component: models.ComponentReading) -> ComponentSnapshot:
    return ComponentSnapshot(
        component_type=component.component_type,
        component_name=component.component_name,
        power_w=component.power_w,
        voltage_v=component.voltage_v,
        current_a=component.current_a,
        energy_today_kwh=component.energy_today_kwh,
        energy_lifetime_kwh=component.energy_lifetime_kwh,
        energy_session_kwh=component.energy_session_kwh,
    )


def energy_interval_snapshot(interval: models.EnergyInterval) -> EnergyIntervalSnapshot:
    return EnergyIntervalSnapshot(
        inverter_id=interval.inverter_id,
        start_at=interval.start_at,
        end_at=interval.end_at,
        generated_kwh=interval.generated_kwh,
        source_counter=interval.source_counter,
        confidence=interval.confidence,
        notes=interval.notes,
    )


def poll_event_snapshot(event: models.PollEvent) -> PollEventSnapshot:
    return PollEventSnapshot(
        inverter_id=event.inverter_id,
        kind=event.kind,
        started_at=event.started_at,
        finished_at=event.finished_at,
        duration_ms=event.duration_ms,
        success=event.success,
        status_code=event.status_code,
        error=event.error,
    )


def alarm_snapshot(alarm: models.AlarmSnapshot) -> AlarmSnapshot:
    return AlarmSnapshot(observed_at=alarm.observed_at, status=alarm.status, alarms=alarm.alarms)


def _ranked_ids(model, observed_column, inverter_ids: list[str], extra_filter=None):
    rank = func.row_number().over(
        partition_by=model.inverter_id,
        order_by=[observed_column.desc(), model.id.desc()],
    )
    stmt = select(model.id.label("id"), rank.label("rank")).where(model.inverter_id.in_(inverter_ids))
    if extra_filter is not None:
        stmt = stmt.where(extra_filter)
    return stmt.subquery()