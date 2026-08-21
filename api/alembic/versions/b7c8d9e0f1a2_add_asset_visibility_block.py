"""add user-controlled asset visibility blocking

Revision ID: b7c8d9e0f1a2
Revises: a7b8c9d0e1f2
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("assets", "is_blocked")
