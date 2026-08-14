from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._energy import uses_reported_daily_counter
from homesolar.archive._queries import filtered_inverters
from homesolar.archive._time import (
    as_utc,
    bucket_key,
    bucket_labels,
    local_date_window_utc,
    range_local_day_count,
    range_start_utc,
)
from homesolar.db import models


def summary_for_range(
    session: Session,
    range_name: str,
    inverter_id: str | None,
    now: datetime | None = None,
) -> dict:
    inverters = filtered_inverters(session, inverter_id)
    now = as_utc(now or datetime.now(UTC))
    days = range_local_day_count(range_name)
    day_labels = {inverter.id: _local_day_labels(inverter, now, days) for inverter in inverters}
    start = min(
        (
            local_date_window_utc(inverter.timezone, date.fromisoformat(day_labels[inverter.id][0]))[0]
            for inverter in inverters
        ),
        default=range_start_utc(range_name, inverters, now),
    )

    reading_stmt = select(models.Reading).where(models.Reading.observed_at >= start)
    if inverter_id:
        reading_stmt = reading_stmt.where(models.Reading.inverter_id == inverter_id)
    readings = session.scalars(reading_stmt).all()

    total_kwh = sum(
        sum(_produced_energy_by_bucket(session, inverter, "daily", day_labels[inverter.id]))
        for inverter in inverters
    )

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


def _local_day_labels(inverter: models.Inverter, now: datetime, days: int) -> list[str]:
    local_today = now.astimezone(ZoneInfo(inverter.timezone)).date()
    return [(local_today - timedelta(days=offset)).isoformat() for offset in reversed(range(days))]


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


def energy_history(
    session: Session,
    period: str,
    inverter_id: str | None,
    start_date: date,
    end_date: date,
) -> dict:
    period = period if period in {"daily", "weekly", "monthly", "yearly"} else "daily"
    labels = _labels_for_date_range(period, start_date, end_date)
    end_date_exclusive = end_date + timedelta(days=1)
    series = [
        {
            "inverter_id": inverter.id,
            "name": inverter.name,
            "data": _produced_energy_by_bucket(
                session,
                inverter,
                period,
                labels,
                start_date=start_date,
                end_date=end_date_exclusive,
            ),
        }
        for inverter in filtered_inverters(session, inverter_id)
    ]
    totals = [
        round(sum(series_item["data"][index] for series_item in series), 3)
        for index in range(len(labels))
    ]
    return {
        "period": period,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "labels": labels,
        "series": series,
        "totals": totals,
    }


def _produced_energy_by_bucket(
    session: Session,
    inverter: models.Inverter,
    period: str,
    labels: list[str],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[float]:
    if not labels:
        return []

    bucket_start_date, bucket_end_date = _bucket_date_range(period, labels)
    start_date = start_date or bucket_start_date
    end_date = end_date or bucket_end_date
    start_utc = local_date_window_utc(inverter.timezone, start_date)[0]
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


def _labels_for_date_range(period: str, start_date: date, end_date: date) -> list[str]:
    if period == "daily":
        return [
            (start_date + timedelta(days=offset)).isoformat()
            for offset in range((end_date - start_date).days + 1)
        ]
    if period == "weekly":
        cursor = start_date - timedelta(days=start_date.weekday())
        labels = []
        while cursor <= end_date:
            labels.append(bucket_key(datetime.combine(cursor, time.min), period))
            cursor += timedelta(days=7)
        return labels
    if period == "monthly":
        cursor = start_date.replace(day=1)
        labels = []
        while cursor <= end_date:
            labels.append(cursor.strftime("%Y-%m"))
            cursor = _next_month(cursor)
        return labels
    return [str(year) for year in range(start_date.year, end_date.year + 1)]
