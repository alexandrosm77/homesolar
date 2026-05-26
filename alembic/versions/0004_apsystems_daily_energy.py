"""store apsystems output energy as daily energy

Revision ID: 0004_apsystems_daily_energy
Revises: 0003_users_and_settings
Create Date: 2026-05-26
"""

from __future__ import annotations

from alembic import op

revision = "0004_apsystems_daily_energy"
down_revision = "0003_users_and_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE readings
        SET energy_today_kwh = energy_session_kwh,
            energy_session_kwh = NULL
        WHERE inverter_id IN (SELECT id FROM inverters WHERE type = 'apsystems_ez1d')
          AND energy_today_kwh IS NULL
          AND energy_session_kwh IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE component_readings
        SET energy_today_kwh = energy_session_kwh,
            energy_session_kwh = NULL
        WHERE inverter_id IN (SELECT id FROM inverters WHERE type = 'apsystems_ez1d')
          AND energy_today_kwh IS NULL
          AND energy_session_kwh IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE energy_intervals
        SET generated_kwh = NULL,
            confidence = 'implausible_delta',
            notes = 'APsystems lifetime delta exceeded plausible short-interval production'
        WHERE inverter_id IN (SELECT id FROM inverters WHERE type = 'apsystems_ez1d')
          AND source_counter = 'lifetime'
          AND confidence = 'normal'
          AND generated_kwh > 0.25
          AND ((julianday(end_at) - julianday(start_at)) * 24 * 60) <= 15
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE readings
        SET energy_session_kwh = energy_today_kwh,
            energy_today_kwh = NULL
        WHERE inverter_id IN (SELECT id FROM inverters WHERE type = 'apsystems_ez1d')
          AND energy_session_kwh IS NULL
          AND energy_today_kwh IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE component_readings
        SET energy_session_kwh = energy_today_kwh,
            energy_today_kwh = NULL
        WHERE inverter_id IN (SELECT id FROM inverters WHERE type = 'apsystems_ez1d')
          AND energy_session_kwh IS NULL
          AND energy_today_kwh IS NOT NULL
        """
    )
