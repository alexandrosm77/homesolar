"""add per-user language preference

Revision ID: 0005_user_language
Revises: 0004_apsystems_daily_energy
Create Date: 2026-05-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_user_language"
down_revision = "0004_apsystems_daily_energy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_users", sa.Column("language", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("app_users", "language")
