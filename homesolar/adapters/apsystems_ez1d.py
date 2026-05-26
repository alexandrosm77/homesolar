from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from homesolar.adapters.base import (
    AdapterResult,
    AlarmState,
    ComponentReading,
    DeviceInfo,
    InverterAdapter,
    NormalizedReading,
    RawContentType,
    RawPayload,
)
from homesolar.config import InverterConfig


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class APsystemsEZ1DAdapter(InverterAdapter):
    supports_alarm = True
    supports_info = True

    def __init__(self, config: InverterConfig, timeout_seconds: float) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    async def _get_json(self, path: str, kind: str) -> tuple[dict[str, Any], RawPayload]:
        url = f"{self.config.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
        raw = RawPayload(
            kind=kind,
            content_type=RawContentType.JSON,
            body=response.text,
            status_code=response.status_code,
        )
        return response.json(), raw

    async def fetch_live(self) -> AdapterResult:
        payload, raw = await self._get_json("/getOutputData", "live")
        observed_at = datetime.now(UTC)
        reading = parse_output_data(payload, observed_at)
        return AdapterResult(raw=raw, reading=reading)

    async def fetch_alarm(self) -> AdapterResult | None:
        payload, raw = await self._get_json("/getAlarm", "alarm")
        observed_at = datetime.now(UTC)
        data = payload.get("data") or {}
        alarms = {
            "off_grid": str(data.get("og")) == "1",
            "output_fault": str(data.get("oe")) == "1",
            "dc1_short_circuit": str(data.get("isce1")) == "1",
            "dc2_short_circuit": str(data.get("isce2")) == "1",
        }
        alarm = AlarmState(
            observed_at=observed_at,
            status="alarm" if any(alarms.values()) else "normal",
            alarms=alarms,
            extra={"device_id": payload.get("deviceId"), "raw_data": data},
        )
        return AdapterResult(raw=raw, alarm=alarm)

    async def fetch_info(self) -> AdapterResult | None:
        payload, raw = await self._get_json("/getDeviceInfo", "info")
        observed_at = datetime.now(UTC)
        data = payload.get("data") or {}
        info = DeviceInfo(
            observed_at=observed_at,
            device_id=data.get("deviceId") or payload.get("deviceId"),
            firmware=data.get("devVer"),
            model="APsystems EZ1D",
            ip_address=data.get("ipAddr"),
            min_power_w=_float_or_none(data.get("minPower")),
            max_power_w=_float_or_none(data.get("maxPower")),
            extra={"ssid": data.get("ssid"), "raw_data": data},
        )
        return AdapterResult(raw=raw, info=info)


def parse_output_data(payload: dict[str, Any], observed_at: datetime) -> NormalizedReading:
    data = payload.get("data") or {}

    p1 = _float_or_none(data.get("p1"))
    p2 = _float_or_none(data.get("p2"))
    e1 = _float_or_none(data.get("e1"))
    e2 = _float_or_none(data.get("e2"))
    te1 = _float_or_none(data.get("te1"))
    te2 = _float_or_none(data.get("te2"))

    components = [
        ComponentReading(
            component_type="channel",
            component_name="channel_1",
            power_w=p1,
            energy_today_kwh=e1,
            energy_lifetime_kwh=te1,
        ),
        ComponentReading(
            component_type="channel",
            component_name="channel_2",
            power_w=p2,
            energy_today_kwh=e2,
            energy_lifetime_kwh=te2,
        ),
    ]

    return NormalizedReading(
        observed_at=observed_at,
        current_power_w=sum(v for v in [p1, p2] if v is not None),
        energy_today_kwh=sum(v for v in [e1, e2] if v is not None),
        energy_lifetime_kwh=sum(v for v in [te1, te2] if v is not None),
        status=payload.get("message"),
        components=components,
        extra={"device_id": payload.get("deviceId"), "source": "getOutputData"},
    )
