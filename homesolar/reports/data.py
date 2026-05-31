from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.db import models


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def yesterday_window(timezone: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """Return (start_utc, end_utc, iso_date_label) for the prior local day."""
    tz = ZoneInfo(timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    today_local = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    start_local = today_local - timedelta(days=1)
    return start_local.astimezone(UTC), today_local.astimezone(UTC), start_local.date().isoformat()


def inverter_day_metrics(
    session: Session, inverter: models.Inverter, start_utc: datetime, end_utc: datetime
) -> dict:
    """Compute production metrics for a single inverter over a UTC window."""
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

    powers = [(r.observed_at, r.current_power_w) for r in readings if r.current_power_w is not None]
    peak_power_w = peak_at_local = average_power_w = None
    first_local = last_local = None
    if powers:
        peak_observed, peak_power_w = max(powers, key=lambda item: item[1])
        peak_at_local = _as_utc(peak_observed).astimezone(tz)
        average_power_w = round(sum(value for _, value in powers) / len(powers), 1)
        producing = [observed for observed, value in powers if value > 0]
        if producing:
            first_local = _as_utc(min(producing)).astimezone(tz)
            last_local = _as_utc(max(producing)).astimezone(tz)

    counter_kwh = max(
        (r.energy_today_kwh for r in readings if r.energy_today_kwh is not None),
        default=0.0,
    )
    interval_total = sum(
        interval.generated_kwh or 0.0
        for interval in session.scalars(
            select(models.EnergyInterval)
            .where(models.EnergyInterval.inverter_id == inverter.id)
            .where(models.EnergyInterval.end_at >= start_utc)
            .where(models.EnergyInterval.end_at < end_utc)
            .where(models.EnergyInterval.confidence == "normal")
        ).all()
    )
    total_kwh = counter_kwh if counter_kwh > 0 else interval_total

    lifetime_kwh = next(
        (r.energy_lifetime_kwh for r in reversed(readings) if r.energy_lifetime_kwh is not None),
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
        "power_points": [(_as_utc(observed).astimezone(tz), value) for observed, value in powers],
    }


def daily_history(
    session: Session,
    inverter: models.Inverter,
    days: int,
    now: datetime | None = None,
) -> list[tuple[str, float]]:
    """Return [(local_date_iso, kwh)] for the last ``days`` days ending yesterday."""
    tz = ZoneInfo(inverter.timezone)
    local_now = (now or datetime.now(UTC)).astimezone(tz)
    today_local = datetime.combine(local_now.date(), time.min, tzinfo=tz)
    window_start = (today_local - timedelta(days=days)).astimezone(UTC)
    readings = session.scalars(
        select(models.Reading)
        .where(models.Reading.inverter_id == inverter.id)
        .where(models.Reading.observed_at >= window_start)
        .where(models.Reading.observed_at < today_local.astimezone(UTC))
        .where(models.Reading.energy_today_kwh.is_not(None))
        .order_by(models.Reading.observed_at)
    ).all()

    totals: dict[str, float] = {}
    for reading in readings:
        local_date = _as_utc(reading.observed_at).astimezone(tz).date().isoformat()
        totals[local_date] = max(totals.get(local_date, 0.0), reading.energy_today_kwh or 0.0)

    labels = [
        (today_local - timedelta(days=offset)).date().isoformat()
        for offset in reversed(range(1, days + 1))
    ]
    return [(label, round(totals.get(label, 0.0), 3)) for label in labels]
