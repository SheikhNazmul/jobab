"""add bundles

Revision ID: b2c4d6e8f0a1
Revises: cd4b10531d87
Create Date: 2026-11-15 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c4d6e8f0a1'
down_revision: Union[str, Sequence[str], None] = 'cd4b10531d87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bundles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('shop_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), server_default='', nullable=False),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('trigger_product_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('price >= 0', name=op.f('ck_bundles_bundle_price_non_negative')),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], name=op.f('fk_bundles_shop_id_shops'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_bundles')),
    )
    op.create_index(op.f('ix_bundles_shop_id'), 'bundles', ['shop_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_bundles_shop_id'), table_name='bundles')
    op.drop_table('bundles')
