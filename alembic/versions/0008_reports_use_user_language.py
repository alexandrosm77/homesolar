"""use the user language preference for daily reports

Revision ID: 0008_reports_use_user_language
Revises: 0007_inverter_live_poll_seconds
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_reports_use_user_language"
down_revision = "0007_inverter_live_poll_seconds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("app_users", "report_language")


def downgrade() -> None:
    op.add_column("app_users", sa.Column("report_language", sa.String(length=8), nullable=True))
