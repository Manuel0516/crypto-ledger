"""add precise price provenance

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite DDL can persist an earlier ADD COLUMN when a later operation in
    # the same Alembic revision fails. Re-running the revision must therefore
    # finish the remaining columns rather than fail on the first one that is
    # already present (the normal recovery path after an interrupted upgrade).
    bind = op.get_bind()
    valuation_columns = {column["name"] for column in sa.inspect(bind).get_columns("valuations")}
    observation_columns = {column["name"] for column in sa.inspect(bind).get_columns("price_observations")}

    if "granularity" not in valuation_columns:
        op.add_column("valuations", sa.Column("granularity", sa.String(), nullable=False, server_default="day"))
    if "fetched_at" not in valuation_columns:
        if bind.dialect.name == "sqlite":
            # SQLite allows only constant defaults in ALTER TABLE ADD COLUMN.
            # Add/backfill instead; the ORM supplies utcnow for every future
            # valuation, while the backfill makes the existing evidence
            # complete and auditable.
            op.add_column("valuations", sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True))
            bind.execute(sa.text("UPDATE valuations SET fetched_at = CURRENT_TIMESTAMP WHERE fetched_at IS NULL"))
        else:
            op.add_column(
                "valuations",
                sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            )
    if "observation_timestamp" not in observation_columns:
        op.add_column("price_observations", sa.Column("observation_timestamp", sa.DateTime(timezone=True), nullable=True))
    if "granularity" not in observation_columns:
        op.add_column("price_observations", sa.Column("granularity", sa.String(), nullable=False, server_default="day"))


def downgrade():
    bind = op.get_bind()
    valuation_columns = {column["name"] for column in sa.inspect(bind).get_columns("valuations")}
    observation_columns = {column["name"] for column in sa.inspect(bind).get_columns("price_observations")}
    if "granularity" in observation_columns:
        op.drop_column("price_observations", "granularity")
    if "observation_timestamp" in observation_columns:
        op.drop_column("price_observations", "observation_timestamp")
    if "fetched_at" in valuation_columns:
        op.drop_column("valuations", "fetched_at")
    if "granularity" in valuation_columns:
        op.drop_column("valuations", "granularity")
