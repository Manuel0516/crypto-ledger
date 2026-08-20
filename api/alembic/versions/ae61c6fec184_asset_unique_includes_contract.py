"""asset unique includes contract

Revision ID: ae61c6fec184
"""
from alembic import op
import sqlalchemy as sa

revision = 'ae61c6fec184'
down_revision = 'ac2f2dbf772b'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('assets') as batch_op:
        batch_op.drop_constraint('uq_asset_symbol_network', type_='unique')
        batch_op.create_unique_constraint('uq_asset_symbol_network_contract', ['symbol', 'network', 'contract_address'])

def downgrade():
    with op.batch_alter_table('assets') as batch_op:
        batch_op.drop_constraint('uq_asset_symbol_network_contract', type_='unique')
        batch_op.create_unique_constraint('uq_asset_symbol_network', ['symbol', 'network'])
