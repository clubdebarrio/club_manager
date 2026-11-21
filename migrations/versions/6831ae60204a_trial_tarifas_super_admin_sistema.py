"""trial, tarifas, super admin sistema

Revision ID: 6831ae60204a
Revises: 62529ff27e04
Create Date: 2026-10-07 18:16:29.620904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6831ae60204a'
down_revision = '62529ff27e04'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Tabla tarifas (nueva, sin problema)
    op.create_table(
        'tarifas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=50), nullable=False),
        sa.Column('socios_desde', sa.Integer(), nullable=False),
        sa.Column('socios_hasta', sa.Integer(), nullable=True),
        sa.Column('precio_mensual', sa.Numeric(precision=10, scale=2),
                  nullable=False),
        sa.Column('activo', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column('orden', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tarifas')),
    )

    # 2. Usuario: es_super_admin_sistema
    op.add_column(
        'usuarios',
        sa.Column('es_super_admin_sistema', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    # 3. Empresa: nuevas columnas
    op.add_column(
        'empresa',
        sa.Column('activo', sa.Boolean(), nullable=False,
                  server_default=sa.true()),
    )
    op.add_column(
        'empresa',
        sa.Column('es_club_fantasma', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column(
        'empresa',
        sa.Column('fecha_alta', sa.DateTime(), nullable=False,
                  server_default=sa.func.now()),
    )
    op.add_column(
        'empresa',
        sa.Column('plan', sa.String(length=20), nullable=False,
                  server_default='trial'),
    )
    op.add_column(
        'empresa',
        sa.Column('estado_pago', sa.String(length=20), nullable=False,
                  server_default='trial'),
    )
    op.add_column(
        'empresa',
        sa.Column('fecha_vencimiento_prueba', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'empresa',
        sa.Column('id_tarifa', sa.Integer(), nullable=True),
    )
    op.add_column(
        'empresa',
        sa.Column('socios_al_cierre', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_empresa_id_tarifa_tarifas', 'empresa', 'tarifas',
        ['id_tarifa'], ['id'],
    )

    # 4. Sacar server_default de columnas que ya tienen datos
    op.alter_column('usuarios', 'es_super_admin_sistema',
                    server_default=None)
    op.alter_column('empresa', 'activo', server_default=None)
    op.alter_column('empresa', 'es_club_fantasma', server_default=None)
    op.alter_column('empresa', 'plan', server_default=None)
    op.alter_column('empresa', 'estado_pago', server_default=None)

    # 5. Actualizar empresas existentes: setear fecha_alta y trial
    op.execute("""
        UPDATE empresa
        SET fecha_alta = COALESCE(fecha_alta, NOW()),
            fecha_vencimiento_prueba = NOW() + INTERVAL '30 days',
            plan = 'trial',
            estado_pago = 'trial'
        WHERE plan = 'trial' OR plan IS NULL
    """)


def downgrade():
    op.drop_constraint('fk_empresa_id_tarifa_tarifas', 'empresa',
                       type_='foreignkey')
    op.drop_column('empresa', 'socios_al_cierre')
    op.drop_column('empresa', 'id_tarifa')
    op.drop_column('empresa', 'fecha_vencimiento_prueba')
    op.drop_column('empresa', 'estado_pago')
    op.drop_column('empresa', 'plan')
    op.drop_column('empresa', 'fecha_alta')
    op.drop_column('empresa', 'es_club_fantasma')
    op.drop_column('empresa', 'activo')
    op.drop_column('usuarios', 'es_super_admin_sistema')
    op.drop_table('tarifas')