"""remove the duplicate destination label and use the activity to value

Revision ID: b8c9d0e1f2a3
Revises: b7c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa


revision = "b8c9d0e1f2a3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep any named destination that was stored before the field was
    # removed, but never overwrite an existing connector-provided `to`
    # address. The destination label then has no independent meaning.
    op.execute(
        sa.text(
            "UPDATE events "
            "SET address_to = destination_label "
            "WHERE (address_to IS NULL OR address_to = '') "
            "AND destination_label IS NOT NULL "
            "AND destination_label <> ''"
        )
    )
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_column("destination_label")


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("destination_label", sa.String(), nullable=True))
