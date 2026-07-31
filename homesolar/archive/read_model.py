from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_LIVE_POLL_SECONDS = 60
STALE_LIVE_POLL_INTERVALS = 10
STALE_LIVE_POLL_MIN_AGE_SECONDS = 900


def stale_after_seconds(live_poll_seconds: int | None) -> int:
    interval = live_poll_seconds or DEFAULT_LIVE_POLL_SECONDS
    return max(interval * STALE_LIVE_POLL_INTERVALS, STALE_LIVE_POLL_MIN_AGE_SECONDS)


@dataclass(frozen=True, slots=True)
class InverterIdentity:
    id: str
    name: str
    type: str
    base_url: str
    enabled: bool
    timezone: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReadingSnapshot:
    id: int
    inverter_id: str
    observed_at: datetime
    current_power_w: float | None
    energy_today_kwh: float | None
    energy_lifetime_kwh: float | None
    energy_session_kwh: float | None
    status: str | None
    extra: dict | None


@dataclass(frozen=True, slots=True)
class ComponentSnapshot:
    component_type: str
    component_name: str
    power_w: float | None
    voltage_v: float | None
    current_a: float | None
    energy_today_kwh: float | None
    energy_lifetime_kwh: float | None
    energy_session_kwh: float | None


@dataclass(frozen=True, slots=True)
class EnergyIntervalSnapshot:
    inverter_id: str
    start_at: datetime
    end_at: datetime
    generated_kwh: float | None
    source_counter: str | None
    confidence: str
    notes: str | None


@dataclass(frozen=True, slots=True)
class PollEventSnapshot:
    inverter_id: str
    kind: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    success: bool
    status_code: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class PowerPoint:
    observed_at: datetime
    power_w: float


@dataclass(frozen=True, slots=True)
class PowerSeries:
    inverter_id: str
    name: str
    points: list[PowerPoint]


@dataclass(frozen=True, slots=True)
class PowerChartSnapshot:
    range_name: str
    series: list[PowerSeries]


@dataclass(frozen=True, slots=True)
class AlarmSnapshot:
    observed_at: datetime
    status: str
    alarms: dict


@dataclass(frozen=True, slots=True)
class TelemetryHealth:
    is_online: bool
    state: str
    age_seconds: int | None
    seen_age_seconds: int | None
    live_poll_age_seconds: int | None = None
    stale_after_seconds: int | None = None

    @property
    def is_stale(self) -> bool:
        return self.state == "stale"


@dataclass(frozen=True, slots=True)
class InverterSnapshot:
    inverter: InverterIdentity
    latest: ReadingSnapshot | None
    produced_energy_today_kwh: float | None
    last_poll: PollEventSnapshot | None
    latest_alarm: AlarmSnapshot | None
    components: list[ComponentSnapshot] = field(default_factory=list)
    health: TelemetryHealth | None = None
    last_live_poll: PollEventSnapshot | None = None


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    total_power_w: float
    total_today_kwh: float
    inverters: list[InverterSnapshot]
    online_count: int
    alarm_count: int
    poll_error_count: int
    updated_at: datetime
    recent_events: list[PollEventSnapshot]
    stale_count: int = 0

    @property
    def total_count(self) -> int:
        return len(self.inverters)