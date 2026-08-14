from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session, sessionmaker

from homesolar.archive import _aggregates, _charts, _energy, _reports, _telemetry
from homesolar.archive._time import as_utc
from homesolar.archive.read_model import (
    DashboardSnapshot,
    EnergyIntervalSnapshot,
    PollEventSnapshot,
    PowerChartSnapshot,
    ReadingSnapshot,
)
from homesolar.db import models


class Archive:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def dashboard_snapshot(self, now: datetime | None = None) -> DashboardSnapshot:
        with self._session_factory() as session:
            return _telemetry.dashboard_snapshot(session, now)

    def median_daily_kwh(
        self, inverter_id: str | None = None, days: int = 14, now: datetime | None = None
    ) -> float | None:
        now = as_utc(now or datetime.now(UTC))
        with self._session_factory() as session:
            return _energy.median_daily_kwh(session, inverter_id, days, now)

    def power_chart_today(
        self, inverter_id: str | None = None, now: datetime | None = None
    ) -> PowerChartSnapshot:
        return self.power_chart("today", inverter_id=inverter_id, now=now)

    def readings(
        self,
        inverter_id: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 500,
    ) -> list[ReadingSnapshot]:
        with self._session_factory() as session:
            return _telemetry.readings(session, inverter_id=inverter_id, from_=from_, to=to, limit=limit)

    def energy_intervals(
        self,
        inverter_id: str | None = None,
        from_: datetime | None = None,
        to: datetime | None = None,
        limit: int = 1000,
    ) -> list[EnergyIntervalSnapshot]:
        with self._session_factory() as session:
            return _telemetry.energy_intervals(
                session, inverter_id=inverter_id, from_=from_, to=to, limit=limit
            )

    def poll_events(self, inverter_id: str | None = None, limit: int = 200) -> list[PollEventSnapshot]:
        with self._session_factory() as session:
            return _telemetry.poll_events(session, inverter_id=inverter_id, limit=limit)

    def power_chart(
        self, range_name: str, inverter_id: str | None = None, now: datetime | None = None
    ) -> PowerChartSnapshot:
        with self._session_factory() as session:
            return _charts.power_chart(session, range_name, inverter_id=inverter_id, now=now)

    def component_chart(
        self, inverter_id: str, range_name: str, metric: str, metric_catalog: dict
    ) -> dict | None:
        with self._session_factory() as session:
            return _charts.component_chart(session, inverter_id, range_name, metric, metric_catalog)

    def summary_for_range(
        self,
        range_name: str,
        inverter_id: str | None = None,
        now: datetime | None = None,
    ) -> dict:
        with self._session_factory() as session:
            return _aggregates.summary_for_range(session, range_name, inverter_id, now)

    def aggregate_energy(
        self,
        period: str,
        inverter_id: str | None = None,
        limit: int = 14,
        now: datetime | None = None,
    ) -> dict:
        with self._session_factory() as session:
            return _aggregates.aggregate_energy(session, period, inverter_id, limit, now)

    def energy_history(
        self,
        period: str,
        start_date: date,
        end_date: date,
        inverter_id: str | None = None,
    ) -> dict:
        with self._session_factory() as session:
            return _aggregates.energy_history(
                session,
                period,
                inverter_id,
                start_date,
                end_date,
            )

    def historical_day(
        self,
        local_date: date,
        inverter_id: str | None,
        component_metric: str,
        metric_catalog: dict,
    ) -> dict:
        with self._session_factory() as session:
            return _reports.historical_day(
                session,
                local_date,
                inverter_id,
                component_metric,
                metric_catalog,
            )

    def inverter_day_metrics(
        self, inverter_id: str, start_utc: datetime, end_utc: datetime
    ) -> dict | None:
        with self._session_factory() as session:
            inverter = session.get(models.Inverter, inverter_id)
            if inverter is None:
                return None
            return _reports.inverter_day_metrics(session, inverter, start_utc, end_utc)

    def daily_history(
        self, inverter_id: str, days: int, now: datetime | None = None
    ) -> list[tuple[str, float]]:
        with self._session_factory() as session:
            inverter = session.get(models.Inverter, inverter_id)
            if inverter is None:
                return []
            return _reports.daily_history(session, inverter, days, now)
