from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._energy import produced_energy_for_date_label, produced_energy_for_window
from homesolar.archive._time import as_utc
from homesolar.db import models


def inverter_day_metrics(
    session: Session, inverter: models.Inverter, start_utc: datetime, end_utc: datetime
) -> dict:
    tz = ZoneInfo(inverter.timezone)
    readings = list(
        session.scalars(
            select(models.Reading)
            .where(models.Reading.inverter_id == inverter.id)
            .where(models.Reading.observed_at >= start_utc)
            .where(models.Reading.observed_at < end_utc)
            .order_by(models.Reading.observed_at)
        ).all()
    )
    powers = [
        (reading.observed_at, reading.current_power_w)
        for reading in readings
        if reading.current_power_w is not None
    ]
    peak_power_w = peak_at_local = average_power_w = None
    first_local = last_local = None
    if powers:
        peak_observed, peak_power_w = max(powers, key=lambda item: item[1])
        peak_at_local = as_utc(peak_observed).astimezone(tz)
        average_power_w = round(sum(value for _, value in powers) / len(powers), 1)
        producing = [observed for observed, value in powers if value > 0]
        if producing:
            first_local = as_utc(min(producing)).astimezone(tz)
            last_local = as_utc(max(producing)).astimezone(tz)
    total_kwh = produced_energy_for_window(session, inverter.id, start_utc, end_utc) or 0.0
    lifetime_kwh = next(
        (reading.energy_lifetime_kwh for reading in reversed(readings) if reading.energy_lifetime_kwh is not None),
        None,
    )
    return {
        "inverter_id": inverter.id,
        "inverter_name": inverter.name,
        "timezone": inverter.timezone,
        "total_kwh": round(float(total_kwh), 3),
        "peak_power_w": round(float(peak_power_w), 1) if peak_power_w is not None else None,
        "peak_at_local": peak_at_local,
        "average_power_w": average_power_w,
        "first_production_local": first_local,
        "last_production_local": last_local,
        "lifetime_kwh": round(float(lifetime_kwh), 3) if lifetime_kwh is not None else None,
        "sample_count": len(readings),
        "power_points": [(as_utc(observed).astimezone(tz), value) for observed, value in powers],
    }


def daily_history(
    session: Session, inverter: models.Inverter, days: int, now: datetime | None = None
) -> list[tuple[str, float]]:
    tz = ZoneInfo(inverter.timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    today_local = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    labels = [(today_local - timedelta(days=offset)).date().isoformat() for offset in reversed(range(1, days + 1))]
    return [
        (label, round(produced_energy_for_date_label(session, inverter, label) or 0.0, 3))
        for label in labels
    ]