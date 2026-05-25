from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Inverter(Base):
    __tablename__ = "inverters"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    readings: Mapped[list[Reading]] = relationship(back_populates="inverter")


class RawPayload(Base):
    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        Index("ix_readings_inverter_observed", "inverter_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_power_w: Mapped[float | None] = mapped_column(Float)
    energy_today_kwh: Mapped[float | None] = mapped_column(Float)
    energy_lifetime_kwh: Mapped[float | None] = mapped_column(Float)
    energy_session_kwh: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(200))
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    extra: Mapped[dict | None] = mapped_column(JSON)

    inverter: Mapped[Inverter] = relationship(back_populates="readings")


class ComponentReading(Base):
    __tablename__ = "component_readings"
    __table_args__ = (
        Index("ix_component_readings_inverter_observed", "inverter_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reading_id: Mapped[int | None] = mapped_column(ForeignKey("readings.id"))
    component_type: Mapped[str] = mapped_column(String(40), index=True)
    component_name: Mapped[str] = mapped_column(String(80), index=True)
    power_w: Mapped[float | None] = mapped_column(Float)
    voltage_v: Mapped[float | None] = mapped_column(Float)
    current_a: Mapped[float | None] = mapped_column(Float)
    energy_today_kwh: Mapped[float | None] = mapped_column(Float)
    energy_lifetime_kwh: Mapped[float | None] = mapped_column(Float)
    energy_session_kwh: Mapped[float | None] = mapped_column(Float)


class EnergyInterval(Base):
    __tablename__ = "energy_intervals"
    __table_args__ = (
        Index("ix_energy_intervals_inverter_start", "inverter_id", "start_at"),
        UniqueConstraint("inverter_id", "start_reading_id", "end_reading_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    start_reading_id: Mapped[int] = mapped_column(ForeignKey("readings.id"))
    end_reading_id: Mapped[int] = mapped_column(ForeignKey("readings.id"))
    generated_kwh: Mapped[float | None] = mapped_column(Float)
    source_counter: Mapped[str | None] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(40), default="normal", nullable=False)
    notes: Mapped[str | None] = mapped_column(String(300))


class PollEvent(Base):
    __tablename__ = "poll_events"
    __table_args__ = (
        Index("ix_poll_events_inverter_started", "inverter_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class AlarmSnapshot(Base):
    __tablename__ = "alarm_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    alarms: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    extra: Mapped[dict | None] = mapped_column(JSON)


class DeviceInfoSnapshot(Base):
    __tablename__ = "device_info_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inverter_id: Mapped[str] = mapped_column(ForeignKey("inverters.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    device_id: Mapped[str | None] = mapped_column(String(120))
    firmware: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(120))
    ip_address: Mapped[str | None] = mapped_column(String(120))
    min_power_w: Mapped[float | None] = mapped_column(Float)
    max_power_w: Mapped[float | None] = mapped_column(Float)
    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    extra: Mapped[dict | None] = mapped_column(JSON)
