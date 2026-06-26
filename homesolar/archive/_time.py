from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from homesolar.db import models


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_day_window_utc(timezone: str, now: datetime) -> tuple[datetime, datetime]:
    return local_date_window_utc(timezone, now.astimezone(ZoneInfo(timezone)).date())


def local_date_window_utc(timezone: str, local_date: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    local_start = datetime.combine(local_date, time.min, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def range_start_utc(
    range_name: str, inverters: list[models.Inverter], now: datetime
) -> datetime:
    if range_name == "today" and inverters:
        return min(local_day_window_utc(inverter.timezone, now)[0] for inverter in inverters)
    ranges = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "365d": timedelta(days=365),
    }
    return now - ranges.get(range_name, timedelta(days=1))


def aggregate_start(period: str, now: datetime, limit: int) -> datetime:
    if period == "daily":
        return now - timedelta(days=limit)
    if period == "weekly":
        return now - timedelta(weeks=limit)
    if period == "monthly":
        return now - timedelta(days=32 * limit)
    return now - timedelta(days=370 * limit)


def bucket_labels(period: str, now: datetime, limit: int) -> list[str]:
    if period == "daily":
        return [(now - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in reversed(range(limit))]
    if period == "weekly":
        return [bucket_key(now - timedelta(weeks=offset), period) for offset in reversed(range(limit))]
    if period == "monthly":
        labels = []
        year = now.year
        month = now.month
        for _ in range(limit):
            labels.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                year -= 1
                month = 12
        return list(reversed(labels))
    return [str(now.year - offset) for offset in reversed(range(limit))]


def bucket_key(value: datetime, period: str) -> str:
    if period == "daily":
        return value.strftime("%Y-%m-%d")
    if period == "weekly":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "monthly":
        return value.strftime("%Y-%m")
    return value.strftime("%Y")