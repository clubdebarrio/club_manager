"""reservas: tipo en pagos_alquiler y estados

Revision ID: 87160df02e5f
Revises: 7232dcc970fd
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa


revision = '87160df02e5f'
down_revision = '7232dcc970fd'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Agregar columna con server_default='pago' (necesario para filas existentes)
    op.add_column(
        'pagos_alquiler',
        sa.Column('tipo', sa.String(length=20), nullable=False,
                  server_default='pago'),
    )
    # 2. Sacar el server_default (ya no hace falta, el modelo lo maneja)
    op.alter_column('pagos_alquiler', 'tipo', server_default=None)

    # 3. Migrar estados legacy: 'pendiente' → 'confirmada'
    op.execute("UPDATE reservas SET estado = 'confirmada' WHERE estado = 'pendiente'")


def downgrade():
    op.drop_column('pagos_alquiler', 'tipo')