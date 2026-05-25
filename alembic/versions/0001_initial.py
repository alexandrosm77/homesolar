"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inverters",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_raw_payloads_inverter_id"), "raw_payloads", ["inverter_id"])
    op.create_index(op.f("ix_raw_payloads_kind"), "raw_payloads", ["kind"])
    op.create_index(op.f("ix_raw_payloads_observed_at"), "raw_payloads", ["observed_at"])
    op.create_table(
        "poll_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_poll_events_inverter_id"), "poll_events", ["inverter_id"])
    op.create_index(op.f("ix_poll_events_kind"), "poll_events", ["kind"])
    op.create_index("ix_poll_events_inverter_started", "poll_events", ["inverter_id", "started_at"])
    op.create_table(
        "readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_power_w", sa.Float(), nullable=True),
        sa.Column("energy_today_kwh", sa.Float(), nullable=True),
        sa.Column("energy_lifetime_kwh", sa.Float(), nullable=True),
        sa.Column("energy_session_kwh", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=200), nullable=True),
        sa.Column("raw_payload_id", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_readings_inverter_id"), "readings", ["inverter_id"])
    op.create_index(op.f("ix_readings_observed_at"), "readings", ["observed_at"])
    op.create_index("ix_readings_inverter_observed", "readings", ["inverter_id", "observed_at"])
    op.create_table(
        "component_readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reading_id", sa.Integer(), nullable=True),
        sa.Column("component_type", sa.String(length=40), nullable=False),
        sa.Column("component_name", sa.String(length=80), nullable=False),
        sa.Column("power_w", sa.Float(), nullable=True),
        sa.Column("voltage_v", sa.Float(), nullable=True),
        sa.Column("current_a", sa.Float(), nullable=True),
        sa.Column("energy_today_kwh", sa.Float(), nullable=True),
        sa.Column("energy_lifetime_kwh", sa.Float(), nullable=True),
        sa.Column("energy_session_kwh", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.ForeignKeyConstraint(["reading_id"], ["readings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_component_readings_inverter_observed",
        "component_readings",
        ["inverter_id", "observed_at"],
    )
    op.create_index(
        op.f("ix_component_readings_component_name"), "component_readings", ["component_name"]
    )
    op.create_index(
        op.f("ix_component_readings_component_type"), "component_readings", ["component_type"]
    )
    op.create_index(
        op.f("ix_component_readings_inverter_id"), "component_readings", ["inverter_id"]
    )
    op.create_index(
        op.f("ix_component_readings_observed_at"), "component_readings", ["observed_at"]
    )
    op.create_table(
        "energy_intervals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_reading_id", sa.Integer(), nullable=False),
        sa.Column("end_reading_id", sa.Integer(), nullable=False),
        sa.Column("generated_kwh", sa.Float(), nullable=True),
        sa.Column("source_counter", sa.String(length=40), nullable=True),
        sa.Column("confidence", sa.String(length=40), nullable=False),
        sa.Column("notes", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["end_reading_id"], ["readings.id"]),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.ForeignKeyConstraint(["start_reading_id"], ["readings.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inverter_id", "start_reading_id", "end_reading_id"),
    )
    op.create_index(
        "ix_energy_intervals_inverter_start", "energy_intervals", ["inverter_id", "start_at"]
    )
    op.create_index(op.f("ix_energy_intervals_inverter_id"), "energy_intervals", ["inverter_id"])
    op.create_index(op.f("ix_energy_intervals_start_at"), "energy_intervals", ["start_at"])
    op.create_index(op.f("ix_energy_intervals_end_at"), "energy_intervals", ["end_at"])
    op.create_table(
        "alarm_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("alarms", sa.JSON(), nullable=False),
        sa.Column("raw_payload_id", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alarm_snapshots_inverter_id"), "alarm_snapshots", ["inverter_id"])
    op.create_index(op.f("ix_alarm_snapshots_observed_at"), "alarm_snapshots", ["observed_at"])
    op.create_table(
        "device_info_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inverter_id", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(length=120), nullable=True),
        sa.Column("firmware", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("ip_address", sa.String(length=120), nullable=True),
        sa.Column("min_power_w", sa.Float(), nullable=True),
        sa.Column("max_power_w", sa.Float(), nullable=True),
        sa.Column("raw_payload_id", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["inverter_id"], ["inverters.id"]),
        sa.ForeignKeyConstraint(["raw_payload_id"], ["raw_payloads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_device_info_snapshots_inverter_id"), "device_info_snapshots", ["inverter_id"]
    )
    op.create_index(
        op.f("ix_device_info_snapshots_observed_at"), "device_info_snapshots", ["observed_at"]
    )


def downgrade() -> None:
    op.drop_table("device_info_snapshots")
    op.drop_table("alarm_snapshots")
    op.drop_table("energy_intervals")
    op.drop_table("component_readings")
    op.drop_table("readings")
    op.drop_table("poll_events")
    op.drop_table("raw_payloads")
    op.drop_table("inverters")
