"""empresa: trial_usado + chytapay bloqueado en trial

Revision ID: eec16d84f4e8
Revises: 6831ae60204a
Create Date: 2026-10-08 10:25:45.476825

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'eec16d84f4e8'
down_revision = '6831ae60204a'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column(
        'empresa',
        sa.Column('trial_usado', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.alter_column('empresa', 'trial_usado', server_default=None)

    # Backfill: los clubes que ya no están en trial, ya lo consumieron
    op.execute("""
        UPDATE empresa
        SET trial_usado = TRUE
        WHERE es_club_fantasma = TRUE
           OR plan != 'trial'
           OR (plan = 'trial' AND fecha_vencimiento_prueba < NOW())
    """)


def downgrade():
    op.drop_column('empresa', 'trial_usado')

    # ### end Alembic commands ###
