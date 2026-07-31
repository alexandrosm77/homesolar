"""record the configured live poll interval per inverter

Revision ID: 0007_inverter_live_poll_seconds
Revises: 0006_user_report_prefs
Create Date: 2026-07-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_inverter_live_poll_seconds"
down_revision = "0006_user_report_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inverters", sa.Column("live_poll_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("inverters", "live_poll_seconds")
