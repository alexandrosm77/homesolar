"""add per-user daily email report preferences

Revision ID: 0006_user_report_prefs
Revises: 0005_user_language
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_user_report_prefs"
down_revision = "0005_user_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column(
        "app_users",
        sa.Column("reports_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("app_users", sa.Column("report_language", sa.String(length=8), nullable=True))
    op.add_column("app_users", sa.Column("report_inverter_ids", sa.JSON(), nullable=True))
    op.add_column(
        "app_users",
        sa.Column("last_report_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("app_users", "last_report_sent_at")
    op.drop_column("app_users", "report_inverter_ids")
    op.drop_column("app_users", "report_language")
    op.drop_column("app_users", "reports_enabled")
    op.drop_column("app_users", "email")
