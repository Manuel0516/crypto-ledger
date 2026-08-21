"""add account balance snapshot

Revision ID: b3c4d5e6f7a8
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3c4d5e6f7a8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('accounts', sa.Column('balance_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'account_balances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('asset_id', sa.Integer(), sa.ForeignKey('assets.id'), nullable=False),
        sa.Column('amount', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('account_id', 'asset_id', name='uq_account_balance_account_asset'),
    )
    op.create_index('ix_account_balances_account', 'account_balances', ['account_id'])

def downgrade():
    op.drop_index('ix_account_balances_account', table_name='account_balances')
    op.drop_table('account_balances')
    op.drop_column('accounts', 'balance_synced_at')
