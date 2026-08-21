"""remove redundant activity labels and free-form metadata

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Older records may have had only `source_label`. Preserve that value as
    # the canonical from-wallet value before removing the duplicate label
    # column. This also keeps the displayed wallet name available if its
    # account is deleted later.
    op.execute(
        sa.text(
            "UPDATE events "
            "SET address_from = source_label "
            "WHERE (address_from IS NULL OR address_from = '') "
            "AND source_label IS NOT NULL "
            "AND source_label <> ''"
        )
    )
    with op.batch_alter_table("events") as batch_op:
        for column in (
            "source_label",
            "description",
            "merchant",
            "tags_json",
            "evidence_reference",
            "notes",
        ):
            batch_op.drop_column(column)


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("source_label", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("merchant", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("tags_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("evidence_reference", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))
