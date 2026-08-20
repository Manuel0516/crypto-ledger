"""add settings secrets and RP2 configuration

Revision ID: a1b2c3d4e5f6
Revises: d9e8c7b6a5f4
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "d9e8c7b6a5f4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("price_provider_api_key_encrypted", sa.Text(), nullable=True))
    op.add_column("app_settings", sa.Column("rp2_plugins_json", sa.Text(), nullable=False, server_default='["rp2_es"]'))


def downgrade():
    op.drop_column("app_settings", "rp2_plugins_json")
    op.drop_column("app_settings", "price_provider_api_key_encrypted")
