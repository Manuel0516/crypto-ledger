"""add currency for the minimum activity display filter

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("minimum_activity_currency", sa.String(), nullable=False, server_default="EUR"))


def downgrade():
    op.drop_column("app_settings", "minimum_activity_currency")
