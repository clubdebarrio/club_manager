"""compras: campos de anulación

Revision ID: 49c9cfba66d2
Revises: 894b32eb4808
Create Date: 2026-10-10 14:10:34.710389

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '49c9cfba66d2'
down_revision = '894b32eb4808'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'compras',
        sa.Column('fecha_anulacion', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'compras',
        sa.Column('motivo_anulacion', sa.String(length=200), nullable=True),
    )


def downgrade():
    op.drop_column('compras', 'motivo_anulacion')
    op.drop_column('compras', 'fecha_anulacion')