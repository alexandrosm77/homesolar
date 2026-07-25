from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.archive._queries import filtered_inverters
from homesolar.archive._time import as_utc, range_start_utc
from homesolar.archive.read_model import PowerChartSnapshot, PowerPoint, PowerSeries
from homesolar.db import models


def power_chart(
    session: Session,
    range_name: str,
    inverter_id: str | None = None,
    now: datetime | None = None,
) -> PowerChartSnapshot:
    now = as_utc(now or datetime.now(UTC))
    inverters = filtered_inverters(session, inverter_id)
    if not inverters:
        return PowerChartSnapshot(range_name=range_name, series=[])
    start_utc = range_start_utc(range_name, inverters, now)
    return PowerChartSnapshot(
        range_name=range_name,
        series=[_power_series(session, inverter, start_utc) for inverter in inverters],
    )


def component_chart(
    session: Session, inverter_id: str, range_name: str, metric: str, metric_catalog: dict
) -> dict | None:
    inverter = session.get(models.Inverter, inverter_id)
    if inverter is None:
        return None
    start = range_start_utc(range_name, [inverter], datetime.now(UTC))
    return component_chart_for_window(
        session,
        inverter,
        start,
        None,
        metric,
        metric_catalog,
        range_name=range_name,
    )


def component_chart_for_window(
    session: Session,
    inverter: models.Inverter,
    start_utc: datetime,
    end_utc: datetime | None,
    metric: str,
    metric_catalog: dict,
    *,
    range_name: str,
    fallback_metric: bool = True,
) -> dict:
    stmt = (
        select(models.ComponentReading)
        .where(models.ComponentReading.inverter_id == inverter.id)
        .where(models.ComponentReading.observed_at >= start_utc)
    )
    if end_utc is not None:
        stmt = stmt.where(models.ComponentReading.observed_at < end_utc)
    rows = session.scalars(
        stmt.order_by(
            models.ComponentReading.component_type,
            models.ComponentReading.component_name,
            models.ComponentReading.observed_at,
        )
    ).all()

    available_metrics = [
        {"metric": key, "label": meta["label"], "unit": meta["unit"]}
        for key, meta in metric_catalog.items()
        if any(getattr(row, key) is not None for row in rows)
    ]
    available_metric_keys = {item["metric"] for item in available_metrics}
    if fallback_metric and metric not in available_metric_keys:
        metric = available_metrics[0]["metric"] if available_metrics else "power_w"

    by_component: dict[tuple[str, str], list[models.ComponentReading]] = defaultdict(list)
    for row in rows:
        if getattr(row, metric) is not None:
            by_component[(row.component_type, row.component_name)].append(row)

    metric_meta = metric_catalog[metric]
    return {
        "range": range_name,
        "inverter_id": inverter.id,
        "metric": metric,
        "label": metric_meta["label"],
        "unit": metric_meta["unit"],
        "available_metrics": available_metrics,
        "series": [
            {
                "component_type": component_type,
                "component_name": component_name,
                "name": component_name.replace("_", " ").title(),
                "points": [
                    {"x": as_utc(row.observed_at).isoformat(), "y": getattr(row, metric)}
                    for row in component_rows
                ],
            }
            for (component_type, component_name), component_rows in by_component.items()
        ],
    }


def _power_series(session: Session, inverter: models.Inverter, start_utc: datetime) -> PowerSeries:
    readings = session.scalars(
        select(models.Reading)
        .where(models.Reading.inverter_id == inverter.id)
        .where(models.Reading.observed_at >= start_utc)
        .order_by(models.Reading.observed_at)
    ).all()
    return PowerSeries(
        inverter_id=inverter.id,
        name=inverter.name,
        points=[
            PowerPoint(observed_at=as_utc(row.observed_at), power_w=row.current_power_w)
            for row in readings
            if row.current_power_w is not None
        ],
    )
