"""expand app settings

Revision ID: d9e8c7b6a5f4
Revises: e4f91b0c62a7, f1a2b3c4d5e6
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e8c7b6a5f4"
down_revision = ("e4f91b0c62a7", "f1a2b3c4d5e6")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("app_settings", sa.Column("display_currency", sa.String(), nullable=False, server_default="EUR"))
    op.add_column("app_settings", sa.Column("valuation_currencies_json", sa.Text(), nullable=False, server_default='["EUR", "SEK"]'))
    op.add_column("app_settings", sa.Column("price_provider", sa.String(), nullable=False, server_default="coingecko"))
    op.add_column("app_settings", sa.Column("price_timeout_seconds", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("app_settings", sa.Column("backup_hour_utc", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("app_settings", sa.Column("backup_verify_after_create", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("app_settings", sa.Column("backup_retention_daily", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("app_settings", sa.Column("backup_retention_weekly", sa.Integer(), nullable=False, server_default="4"))
    op.add_column("app_settings", sa.Column("backup_retention_monthly", sa.Integer(), nullable=False, server_default="12"))
    op.add_column("app_settings", sa.Column("ui_theme", sa.String(), nullable=False, server_default="system"))
    op.add_column("app_settings", sa.Column("default_timezone", sa.String(), nullable=False, server_default="UTC"))
    op.add_column("app_settings", sa.Column("evidence_retention_policy", sa.String(), nullable=False, server_default="indefinite"))


def downgrade():
    op.drop_column("app_settings", "evidence_retention_policy")
    op.drop_column("app_settings", "default_timezone")
    op.drop_column("app_settings", "ui_theme")
    op.drop_column("app_settings", "backup_retention_monthly")
    op.drop_column("app_settings", "backup_retention_weekly")
    op.drop_column("app_settings", "backup_retention_daily")
    op.drop_column("app_settings", "backup_verify_after_create")
    op.drop_column("app_settings", "backup_hour_utc")
    op.drop_column("app_settings", "price_timeout_seconds")
    op.drop_column("app_settings", "price_provider")
    op.drop_column("app_settings", "valuation_currencies_json")
    op.drop_column("app_settings", "display_currency")
    op.drop_column("app_settings", "sync_enabled")
