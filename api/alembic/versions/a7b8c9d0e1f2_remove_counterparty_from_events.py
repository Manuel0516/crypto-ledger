"""remove the redundant counterparty activity field

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
"""

from alembic import op
import sqlalchemy as sa


revision = "a7b8c9d0e1f2"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite needs a batch operation to rebuild the table without the
    # redundant column. Existing counterparty values are intentionally
    # discarded because source/destination wallets are now authoritative.
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_column("counterparty")


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("counterparty", sa.String(), nullable=True))
