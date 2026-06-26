from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._energy import produced_energy_for_date_label, produced_energy_today
from homesolar.archive._queries import filtered_inverters
from homesolar.archive._time import aggregate_start, as_utc, bucket_key, bucket_labels, range_start_utc
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


def aggregate_energy(session: Session, period: str, inverter_id: str | None, limit: int) -> dict:
    period = period if period in {"daily", "weekly", "monthly", "yearly"} else "daily"
    inverters = filtered_inverters(session, inverter_id)
    now = datetime.now(UTC)
    labels = bucket_labels(period, now, limit)
    series = []
    if period == "daily":
        for inverter in inverters:
            data = [
                round(produced_energy_for_date_label(session, inverter, label) or 0.0, 3)
                for label in labels
            ]
            series.append({"inverter_id": inverter.id, "name": inverter.name, "data": data})
    else:
        interval_totals = _interval_totals(session, period, inverter_id, now, limit)
        for inverter in inverters:
            data = [round(interval_totals.get((inverter.id, label), 0.0), 3) for label in labels]
            series.append({"inverter_id": inverter.id, "name": inverter.name, "data": data})
    totals = [round(sum(series_item["data"][index] for series_item in series), 3) for index in range(len(labels))]
    return {"period": period, "labels": labels, "series": series, "totals": totals}


def _interval_totals(
    session: Session, period: str, inverter_id: str | None, now: datetime, limit: int
) -> dict[tuple[str, str], float]:
    start = aggregate_start(period, now, limit)
    intervals = session.scalars(
        select(models.EnergyInterval)
        .where(models.EnergyInterval.end_at >= start)
        .where(models.EnergyInterval.confidence == "normal")
        .order_by(models.EnergyInterval.end_at)
    ).all()
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for interval in intervals:
        if inverter_id and interval.inverter_id != inverter_id:
            continue
        key = bucket_key(as_utc(interval.end_at), period)
        totals[(interval.inverter_id, key)] += interval.generated_kwh or 0
    return totals