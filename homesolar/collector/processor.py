from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from homesolar.adapters.base import AdapterResult
from homesolar.config import InverterConfig
from homesolar.db import models


def ensure_inverter(session: Session, config: InverterConfig) -> models.Inverter:
    inverter = session.get(models.Inverter, config.id)
    if inverter is None:
        inverter = next(
            (
                obj
                for obj in session.new
                if isinstance(obj, models.Inverter) and obj.id == config.id
            ),
            None,
        )
    if inverter is None:
        inverter = models.Inverter(
            id=config.id,
            name=config.name,
            type=config.type,
            base_url=config.base_url,
            timezone=config.timezone,
            enabled=config.enabled,
        )
        session.add(inverter)
    else:
        inverter.name = config.name
        inverter.type = config.type
        inverter.base_url = config.base_url
        inverter.timezone = config.timezone
        inverter.enabled = config.enabled
    return inverter


def store_adapter_result(session: Session, config: InverterConfig, result: AdapterResult) -> None:
    inverter = ensure_inverter(session, config)
    observed_at = _observed_at(result)
    if inverter.first_seen_at is None or _as_utc(observed_at) < _as_utc(inverter.first_seen_at):
        inverter.first_seen_at = observed_at
    if inverter.last_seen_at is None or _as_utc(observed_at) > _as_utc(inverter.last_seen_at):
        inverter.last_seen_at = observed_at
    raw_model = models.RawPayload(
        inverter_id=config.id,
        observed_at=observed_at,
        kind=result.raw.kind,
        content_type=str(result.raw.content_type),
        status_code=result.raw.status_code,
        body=result.raw.body,
    )
    session.add(raw_model)
    session.flush()

    if result.reading:
        reading_model = models.Reading(
            inverter_id=config.id,
            observed_at=result.reading.observed_at,
            current_power_w=result.reading.current_power_w,
            energy_today_kwh=result.reading.energy_today_kwh,
            energy_lifetime_kwh=result.reading.energy_lifetime_kwh,
            energy_session_kwh=result.reading.energy_session_kwh,
            status=result.reading.status,
            raw_payload_id=raw_model.id,
            extra=result.reading.extra,
        )
        session.add(reading_model)
        session.flush()

        for component in result.reading.components:
            session.add(
                models.ComponentReading(
                    inverter_id=config.id,
                    observed_at=result.reading.observed_at,
                    reading_id=reading_model.id,
                    component_type=component.component_type,
                    component_name=component.component_name,
                    power_w=component.power_w,
                    voltage_v=component.voltage_v,
                    current_a=component.current_a,
                    energy_today_kwh=component.energy_today_kwh,
                    energy_lifetime_kwh=component.energy_lifetime_kwh,
                    energy_session_kwh=component.energy_session_kwh,
                )
            )

        _store_interval(session, config.id, reading_model)

    if result.alarm:
        session.add(
            models.AlarmSnapshot(
                inverter_id=config.id,
                observed_at=result.alarm.observed_at,
                status=result.alarm.status,
                alarms=result.alarm.alarms,
                raw_payload_id=raw_model.id,
                extra=result.alarm.extra,
            )
        )

    if result.info:
        session.add(
            models.DeviceInfoSnapshot(
                inverter_id=config.id,
                observed_at=result.info.observed_at,
                device_id=result.info.device_id,
                firmware=result.info.firmware,
                model=result.info.model,
                ip_address=result.info.ip_address,
                min_power_w=result.info.min_power_w,
                max_power_w=result.info.max_power_w,
                raw_payload_id=raw_model.id,
                extra=result.info.extra,
            )
        )


def store_poll_event(
    session: Session,
    inverter_id: str,
    kind: str,
    started_at: datetime,
    finished_at: datetime,
    success: bool,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    duration_ms = (finished_at - started_at).total_seconds() * 1000
    session.add(
        models.PollEvent(
            inverter_id=inverter_id,
            kind=kind,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            success=success,
            status_code=status_code,
            error=error,
        )
    )


def _observed_at(result: AdapterResult) -> datetime:
    if result.reading:
        return result.reading.observed_at
    if result.alarm:
        return result.alarm.observed_at
    if result.info:
        return result.info.observed_at
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _store_interval(session: Session, inverter_id: str, current: models.Reading) -> None:
    previous = session.scalars(
        select(models.Reading)
        .where(models.Reading.inverter_id == inverter_id)
        .where(models.Reading.id != current.id)
        .order_by(models.Reading.observed_at.desc())
        .limit(1)
    ).first()
    if previous is None:
        return

    generated, source, confidence, notes = _calculate_delta(previous, current)
    session.add(
        models.EnergyInterval(
            inverter_id=inverter_id,
            start_at=previous.observed_at,
            end_at=current.observed_at,
            start_reading_id=previous.id,
            end_reading_id=current.id,
            generated_kwh=generated,
            source_counter=source,
            confidence=confidence,
            notes=notes,
        )
    )


def _calculate_delta(
    previous: models.Reading, current: models.Reading
) -> tuple[float | None, str | None, str, str | None]:
    if previous.energy_today_kwh is not None and current.energy_today_kwh is not None:
        return _counter_delta(previous, current, "daily")

    if previous.energy_lifetime_kwh is not None and current.energy_lifetime_kwh is not None:
        return _counter_delta(previous, current, "lifetime")

    if previous.energy_session_kwh is not None and current.energy_session_kwh is not None:
        return _counter_delta(previous, current, "session")

    return None, None, "missing_counter", "no comparable cumulative counter"


def _counter_delta(
    previous: models.Reading, current: models.Reading, source: str
) -> tuple[float | None, str, str, str | None]:
    previous_value = getattr(previous, f"energy_{_counter_field(source)}_kwh")
    current_value = getattr(current, f"energy_{_counter_field(source)}_kwh")
    delta = current_value - previous_value
    if delta < 0:
        return None, source, "counter_reset_or_invalid", f"{source} counter went backwards"

    generated = round(delta, 6)
    plausible, note = _is_plausible_delta(previous, current, generated)
    if not plausible:
        return None, source, "implausible_delta", note
    return generated, source, "normal", None


def _counter_field(source: str) -> str:
    return "today" if source == "daily" else source


def _is_plausible_delta(
    previous: models.Reading, current: models.Reading, generated_kwh: float
) -> tuple[bool, str | None]:
    if generated_kwh <= 0:
        return True, None

    elapsed_hours = (_as_utc(current.observed_at) - _as_utc(previous.observed_at)).total_seconds() / 3600
    if elapsed_hours <= 0:
        return True, None

    powers = [
        power
        for power in (previous.current_power_w, current.current_power_w)
        if power is not None and power >= 0
    ]
    if not powers:
        return True, None

    max_expected_kwh = max(powers) / 1000 * elapsed_hours
    allowed_kwh = max(0.25, max_expected_kwh * 6)
    if generated_kwh <= allowed_kwh:
        return True, None

    return (
        False,
        f"delta {generated_kwh:.3f} kWh exceeds plausible {allowed_kwh:.3f} kWh "
        f"for {elapsed_hours:.2f} h interval",
    )
