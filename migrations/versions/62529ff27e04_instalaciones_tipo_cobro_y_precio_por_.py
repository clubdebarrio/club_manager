"""instalaciones: tipo_cobro y precio_por_uso

Revision ID: 62529ff27e04
Revises: 60c53a3f2305
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa


revision = '62529ff27e04'
down_revision = '60c53a3f2305'
branch_labels = None
depends_on = None


def upgrade():
    # 1. tipo_cobro con server_default para que las filas existentes
    #    tomen el valor 'por_hora'.
    op.add_column(
        'instalaciones',
        sa.Column('tipo_cobro', sa.String(length=20), nullable=False,
                  server_default='por_hora'),
    )
    # Sacar el server_default: a partir de ahora el default lo maneja Python
    op.alter_column('instalaciones', 'tipo_cobro', server_default=None)

    # 2. precio_por_uso nullable
    op.add_column(
        'instalaciones',
        sa.Column('precio_por_uso', sa.Numeric(precision=10, scale=2),
                  nullable=True),
    )

    # 3. precio_por_hora pasa a nullable (porque en 'por_uso' no aplica)
    op.alter_column(
        'instalaciones', 'precio_por_hora',
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=True,
    )


def downgrade():
    op.alter_column(
        'instalaciones', 'precio_por_hora',
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=False,
    )
    op.drop_column('instalaciones', 'precio_por_uso')
    op.drop_column('instalaciones', 'tipo_cobro')