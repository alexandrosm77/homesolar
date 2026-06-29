from __future__ import annotations

from datetime import date, datetime, timedelta
import statistics
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from homesolar.archive._queries import filtered_inverters
from homesolar.archive._time import local_date_window_utc, local_day_window_utc
from homesolar.db import models


def produced_energy_today(session: Session, inverter: models.Inverter, now: datetime) -> float | None:
    start_utc, end_utc = local_day_window_utc(inverter.timezone, now)
    if uses_reported_daily_counter(inverter):
        counter = latest_reported_daily_counter(session, inverter.id, start_utc, end_utc)
        if counter is not None:
            return counter
    return produced_energy_for_window(
        session,
        inverter.id,
        start_utc,
        end_utc,
        use_reported_daily_counter=False,
    )


def latest_reported_daily_counter(
    session: Session, inverter_id: str, start_utc: datetime, end_utc: datetime
) -> float | None:
    counter = session.scalar(
        select(models.Reading.energy_today_kwh)
        .where(models.Reading.inverter_id == inverter_id)
        .where(models.Reading.observed_at >= start_utc)
        .where(models.Reading.observed_at < end_utc)
        .where(models.Reading.energy_today_kwh.is_not(None))
        .order_by(models.Reading.observed_at.desc())
        .limit(1)
    )
    return round(float(counter), 3) if counter is not None else None


def produced_energy_for_local_day(
    session: Session, inverter: models.Inverter, now: datetime, days_before_today: int
) -> float:
    local_date = now.astimezone(ZoneInfo(inverter.timezone)).date() - timedelta(days=days_before_today)
    start_utc, end_utc = local_date_window_utc(inverter.timezone, local_date)
    return (
        produced_energy_for_window(
            session,
            inverter.id,
            start_utc,
            end_utc,
            use_reported_daily_counter=uses_reported_daily_counter(inverter),
        )
        or 0.0
    )


def produced_energy_for_window(
    session: Session,
    inverter_id: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    use_reported_daily_counter: bool = True,
) -> float | None:
    if use_reported_daily_counter:
        counter = session.scalar(
            select(func.max(models.Reading.energy_today_kwh))
            .where(models.Reading.inverter_id == inverter_id)
            .where(models.Reading.observed_at >= start_utc)
            .where(models.Reading.observed_at < end_utc)
            .where(models.Reading.energy_today_kwh.is_not(None))
        )
        if counter is not None:
            return round(float(counter), 3)
    total = session.scalar(
        select(func.sum(models.EnergyInterval.generated_kwh))
        .where(models.EnergyInterval.inverter_id == inverter_id)
        .where(models.EnergyInterval.start_at >= start_utc)
        .where(models.EnergyInterval.end_at >= start_utc)
        .where(models.EnergyInterval.end_at < end_utc)
        .where(models.EnergyInterval.confidence == "normal")
    )
    return round(float(total), 3) if total is not None else None


def produced_energy_for_date_label(
    session: Session, inverter: models.Inverter, local_date_label: str
) -> float | None:
    local_date = date.fromisoformat(local_date_label)
    start_utc, end_utc = local_date_window_utc(inverter.timezone, local_date)
    return produced_energy_for_window(
        session,
        inverter.id,
        start_utc,
        end_utc,
        use_reported_daily_counter=uses_reported_daily_counter(inverter),
    )


def uses_reported_daily_counter(inverter: models.Inverter) -> bool:
    return inverter.type != "apsystems_ez1d"


def median_daily_kwh(
    session: Session, inverter_id: str | None, days: int, now: datetime
) -> float | None:
    inverters = filtered_inverters(session, inverter_id)
    daily_totals = [
        sum(produced_energy_for_local_day(session, inverter, now, offset) for inverter in inverters)
        for offset in range(days, 0, -1)
    ]
    return round(statistics.median(daily_totals), 3) if daily_totals else None