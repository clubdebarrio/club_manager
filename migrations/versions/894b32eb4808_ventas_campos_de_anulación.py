"""ventas: campos de anulación

Revision ID: 894b32eb4808
Revises: d68cb52a1683
Create Date: 2026-10-10 12:29:44.113273

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '894b32eb4808'
down_revision = 'd68cb52a1683'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'ventas',
        sa.Column('fecha_anulacion', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'ventas',
        sa.Column('motivo_anulacion', sa.String(length=200), nullable=True),
    )


def downgrade():
    op.drop_column('ventas', 'motivo_anulacion')
    op.drop_column('ventas', 'fecha_anulacion')