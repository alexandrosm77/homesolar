from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._energy import produced_energy_today, uses_reported_daily_counter
from homesolar.archive._queries import filtered_inverters
from homesolar.archive._time import (
    as_utc,
    bucket_key,
    bucket_labels,
    local_date_window_utc,
    range_start_utc,
)
from homesolar.db import models


def summary_for_range(session: Session, range_name: str, inverter_id: str | None) -> dict:
    inverters = filtered_inverters(session, inverter_id)
    now = datetime.now(UTC)
    start = range_start_utc(range_name, inverters, now)
    reading_stmt = select(models.Reading).where(models.Reading.observed_at >= start)
    interval_stmt = select(models.EnergyInterval).where(models.EnergyInterval.end_at >= start)
    if inverter_id:
        reading_stmt = reading_stmt.where(models.Reading.inverter_id == inverter_id)
        interval_stmt = interval_stmt.where(models.EnergyInterval.inverter_id == inverter_id)

    readings = session.scalars(reading_stmt).all()
    if range_name == "today":
        total_kwh = sum(produced_energy_today(session, inverter, now) or 0 for inverter in inverters)
    else:
        intervals = session.scalars(
            interval_stmt.where(models.EnergyInterval.confidence == "normal")
        ).all()
        total_kwh = sum(interval.generated_kwh or 0 for interval in intervals)

    power_values = [row.current_power_w for row in readings if row.current_power_w is not None]
    avg_power = sum(power_values) / len(power_values) if power_values else None
    return {
        "range": range_name,
        "inverter_id": inverter_id,
        "total_kwh": round(total_kwh, 3),
        "peak_power_w": max(power_values) if power_values else None,
        "average_power_w": round(avg_power, 1) if avg_power is not None else None,
        "reading_count": len(readings),
    }


def aggregate_energy(
    session: Session,
    period: str,
    inverter_id: str | None,
    limit: int,
    now: datetime | None = None,
) -> dict:
    period = period if period in {"daily", "weekly", "monthly", "yearly"} else "daily"
    inverters = filtered_inverters(session, inverter_id)
    now = as_utc(now or datetime.now(UTC))
    labels = bucket_labels(period, now, limit)
    series = [
        {
            "inverter_id": inverter.id,
            "name": inverter.name,
            "data": _produced_energy_by_bucket(session, inverter, period, labels),
        }
        for inverter in inverters
    ]
    totals = [
        round(sum(series_item["data"][index] for series_item in series), 3)
        for index in range(len(labels))
    ]
    return {"period": period, "labels": labels, "series": series, "totals": totals}


def _produced_energy_by_bucket(
    session: Session,
    inverter: models.Inverter,
    period: str,
    labels: list[str],
) -> list[float]:
    if not labels:
        return []

    first_date, end_date = _bucket_date_range(period, labels)
    start_utc = local_date_window_utc(inverter.timezone, first_date)[0]
    end_utc = local_date_window_utc(inverter.timezone, end_date)[0]
    timezone = ZoneInfo(inverter.timezone)

    daily_counters: dict[date, float] = {}
    daily_counter_observed_at: dict[date, datetime] = {}
    if uses_reported_daily_counter(inverter):
        readings = session.execute(
            select(models.Reading)
            .with_only_columns(
                models.Reading.observed_at,
                models.Reading.energy_today_kwh,
            )
            .where(models.Reading.inverter_id == inverter.id)
            .where(models.Reading.observed_at >= start_utc)
            .where(models.Reading.observed_at < end_utc)
            .where(models.Reading.energy_today_kwh.is_not(None))
            .execution_options(yield_per=1000)
        )
        for observed_at_value, counter_value in readings:
            observed_at = as_utc(observed_at_value)
            local_date = observed_at.astimezone(timezone).date()
            counter = float(counter_value)
            previous_observed_at = daily_counter_observed_at.get(local_date)
            if previous_observed_at is None or observed_at > previous_observed_at:
                daily_counters[local_date] = counter
                daily_counter_observed_at[local_date] = observed_at

    daily_intervals: dict[date, float] = defaultdict(float)
    intervals = session.execute(
        select(models.EnergyInterval)
        .with_only_columns(
            models.EnergyInterval.start_at,
            models.EnergyInterval.end_at,
            models.EnergyInterval.generated_kwh,
        )
        .where(models.EnergyInterval.inverter_id == inverter.id)
        .where(models.EnergyInterval.start_at >= start_utc)
        .where(models.EnergyInterval.end_at >= start_utc)
        .where(models.EnergyInterval.end_at < end_utc)
        .where(models.EnergyInterval.confidence == "normal")
        .execution_options(yield_per=1000)
    )
    for start_at, end_at, generated_kwh in intervals:
        interval_start = as_utc(start_at)
        interval_end = as_utc(end_at)
        local_date = interval_end.astimezone(timezone).date()
        day_start_utc, day_end_utc = local_date_window_utc(inverter.timezone, local_date)
        if interval_start < day_start_utc or interval_end >= day_end_utc:
            continue
        daily_intervals[local_date] += generated_kwh or 0.0

    bucket_totals: dict[str, float] = defaultdict(float)
    for local_date in daily_counters.keys() | daily_intervals.keys():
        produced_energy = daily_counters.get(local_date, daily_intervals.get(local_date, 0.0))
        key = bucket_key(datetime.combine(local_date, time.min), period)
        bucket_totals[key] += produced_energy

    return [round(bucket_totals.get(label, 0.0), 3) for label in labels]


def _bucket_date_range(period: str, labels: list[str]) -> tuple[date, date]:
    if period == "daily":
        return date.fromisoformat(labels[0]), date.fromisoformat(labels[-1]) + timedelta(days=1)
    if period == "weekly":
        first_year, first_week = labels[0].split("-W")
        last_year, last_week = labels[-1].split("-W")
        return (
            date.fromisocalendar(int(first_year), int(first_week), 1),
            date.fromisocalendar(int(last_year), int(last_week), 1) + timedelta(days=7),
        )
    if period == "monthly":
        first_year, first_month = (int(value) for value in labels[0].split("-"))
        last_year, last_month = (int(value) for value in labels[-1].split("-"))
        return date(first_year, first_month, 1), _next_month(date(last_year, last_month, 1))
    return date(int(labels[0]), 1, 1), date(int(labels[-1]) + 1, 1, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)
