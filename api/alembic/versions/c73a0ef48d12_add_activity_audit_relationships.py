"""add activity audit metadata and event relationships

Revision ID: c73a0ef48d12
Revises: 35f87a631605
"""
from alembic import op
import sqlalchemy as sa


revision = "c73a0ef48d12"
down_revision = "35f87a631605"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("raw_events", sa.Column("source_timezone", sa.String(), nullable=True))
    op.add_column("raw_events", sa.Column("source_reference", sa.String(), nullable=True))

    op.add_column("events", sa.Column("source_timezone", sa.String(), nullable=True))
    op.add_column("events", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("merchant", sa.String(), nullable=True))
    op.add_column("events", sa.Column("tags_json", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("evidence_reference", sa.String(), nullable=True))
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_account_occurred_at", "events", ["account_id", "occurred_at"])
    op.create_index("ix_events_type_occurred_at", "events", ["event_type", "occurred_at"])
    op.create_index("ix_events_provenance_occurred_at", "events", ["provenance", "occurred_at"])
    op.create_index("ix_events_status_occurred_at", "events", ["status", "occurred_at"])

    op.create_table(
        "event_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("linked_event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("relationship_type", sa.String(), nullable=False, server_default="RELATED"),
        sa.Column("provenance", sa.String(), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", "linked_event_id", "relationship_type", name="uq_event_link_pair_type"),
    )
    op.create_index("ix_event_links_linked_event_id", "event_links", ["linked_event_id"])


def downgrade():
    op.drop_index("ix_event_links_linked_event_id", table_name="event_links")
    op.drop_table("event_links")
    op.drop_index("ix_events_status_occurred_at", table_name="events")
    op.drop_index("ix_events_provenance_occurred_at", table_name="events")
    op.drop_index("ix_events_type_occurred_at", table_name="events")
    op.drop_index("ix_events_account_occurred_at", table_name="events")
    op.drop_index("ix_events_occurred_at", table_name="events")
    op.drop_column("events", "evidence_reference")
    op.drop_column("events", "tags_json")
    op.drop_column("events", "merchant")
    op.drop_column("events", "description")
    op.drop_column("events", "source_timezone")
    op.drop_column("raw_events", "source_reference")
    op.drop_column("raw_events", "source_timezone")
