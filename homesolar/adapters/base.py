from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RawContentType(StrEnum):
    JSON = "application/json"
    HTML = "text/html"
    TEXT = "text/plain"


@dataclass(slots=True)
class RawPayload:
    kind: str
    content_type: RawContentType
    body: str
    status_code: int | None = None


@dataclass(slots=True)
class ComponentReading:
    component_type: str
    component_name: str
    power_w: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    energy_today_kwh: float | None = None
    energy_lifetime_kwh: float | None = None
    energy_session_kwh: float | None = None


@dataclass(slots=True)
class NormalizedReading:
    observed_at: datetime
    current_power_w: float | None = None
    energy_today_kwh: float | None = None
    energy_lifetime_kwh: float | None = None
    energy_session_kwh: float | None = None
    status: str | None = None
    components: list[ComponentReading] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AlarmState:
    observed_at: datetime
    status: str
    alarms: dict[str, bool]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceInfo:
    observed_at: datetime
    device_id: str | None = None
    firmware: str | None = None
    model: str | None = None
    ip_address: str | None = None
    min_power_w: float | None = None
    max_power_w: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterResult:
    raw: RawPayload
    reading: NormalizedReading | None = None
    alarm: AlarmState | None = None
    info: DeviceInfo | None = None


class InverterAdapter:
    supports_alarm = False
    supports_info = False

    async def fetch_live(self) -> AdapterResult:
        raise NotImplementedError

    async def fetch_alarm(self) -> AdapterResult | None:
        return None

    async def fetch_info(self) -> AdapterResult | None:
        return None
