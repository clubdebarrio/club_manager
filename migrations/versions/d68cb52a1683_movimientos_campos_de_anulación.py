"""movimientos: campos de anulación

Revision ID: d68cb52a1683
Revises: 4097816f98fb
Create Date: 2026-10-10 08:45:06.177017

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd68cb52a1683'
down_revision = '4097816f98fb'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'movimientos',
        sa.Column('anulado', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        'movimientos',
        sa.Column('es_anulacion', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        'movimientos',
        sa.Column('fecha_anulacion', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'movimientos',
        sa.Column('motivo_anulacion', sa.String(length=200), nullable=True),
    )
    op.add_column(
        'movimientos',
        sa.Column('id_movimiento_anulacion', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_movimientos_id_movimiento_anulacion_movimientos',
        'movimientos', 'movimientos',
        ['id_movimiento_anulacion'], ['id'],
    )

    # Sacar server_default después del backfill
    op.alter_column('movimientos', 'anulado', server_default=None)
    op.alter_column('movimientos', 'es_anulacion', server_default=None)


def downgrade():
    op.drop_constraint(
        'fk_movimientos_id_movimiento_anulacion_movimientos',
        'movimientos', type_='foreignkey',
    )
    op.drop_column('movimientos', 'id_movimiento_anulacion')
    op.drop_column('movimientos', 'motivo_anulacion')
    op.drop_column('movimientos', 'fecha_anulacion')
    op.drop_column('movimientos', 'es_anulacion')
    op.drop_column('movimientos', 'anulado')
