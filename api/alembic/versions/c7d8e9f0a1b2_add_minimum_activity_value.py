"""add configurable minimum activity display value

Revision ID: c7d8e9f0a1b2
Revises: c4d5e6f7a8b9
"""

from alembic import op
import sqlalchemy as sa


revision = "c7d8e9f0a1b2"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("minimum_activity_value", sa.String(), nullable=False, server_default="0.05"))


def downgrade():
    op.drop_column("app_settings", "minimum_activity_value")
