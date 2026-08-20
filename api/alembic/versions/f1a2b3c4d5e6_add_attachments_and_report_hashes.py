"""add attachments and report reproducibility hashes

Revision ID: f1a2b3c4d5e6
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1a2b3c4d5e6'
down_revision = 'b4c6bd5c2475'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('tax_reports', sa.Column('price_dataset_hash', sa.String(), nullable=False, server_default=''))
    op.add_column('tax_reports', sa.Column('output_hashes_json', sa.Text(), nullable=False, server_default='{}'))

    op.create_table(
        'attachments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('events.id'), nullable=True),
        sa.Column('kind', sa.String(), nullable=False, server_default='other'),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('content_type', sa.String(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=False),
        sa.Column('storage_path', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_attachments_event_id', 'attachments', ['event_id'])

def downgrade():
    op.drop_index('ix_attachments_event_id', table_name='attachments')
    op.drop_table('attachments')
    op.drop_column('tax_reports', 'output_hashes_json')
    op.drop_column('tax_reports', 'price_dataset_hash')
