"""add Activity query indexes

Revision ID: e4f91b0c62a7
Revises: c73a0ef48d12
"""
from alembic import op


revision = "e4f91b0c62a7"
down_revision = "c73a0ef48d12"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_assets_network", "assets", ["network"])
    op.create_index("ix_events_asset_occurred_at", "events", ["primary_asset_id", "occurred_at"])


def downgrade():
    op.drop_index("ix_events_asset_occurred_at", table_name="events")
    op.drop_index("ix_assets_network", table_name="assets")
