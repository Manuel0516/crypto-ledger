"""allow internal transfers without a linked counterpart

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("internal_transfer", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("events", "internal_transfer")
