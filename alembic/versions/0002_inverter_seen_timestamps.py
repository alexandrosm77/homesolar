"""track inverter first and last seen timestamps

Revision ID: 0002_inverter_seen_timestamps
Revises: 0001_initial
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_inverter_seen_timestamps"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inverters", sa.Column("first_seen_at", sa.DateTime(timezone=True)))
    op.add_column("inverters", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.execute(
        """
        UPDATE inverters
        SET
          first_seen_at = (
            SELECT MIN(readings.observed_at)
            FROM readings
            WHERE readings.inverter_id = inverters.id
          ),
          last_seen_at = (
            SELECT MAX(readings.observed_at)
            FROM readings
            WHERE readings.inverter_id = inverters.id
          )
        """
    )


def downgrade() -> None:
    op.drop_column("inverters", "last_seen_at")
    op.drop_column("inverters", "first_seen_at")
