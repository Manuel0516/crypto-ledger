"""store explorer API keys in application settings

Revision ID: f2a3b4c5d6e7
Revises: e5f6a7b8c9d0
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("app_settings", sa.Column("explorer_api_keys_encrypted", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("app_settings", "explorer_api_keys_encrypted")
