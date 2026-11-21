"""socios: foto_actualizada_at

Revision ID: cb4cc5211b23
Revises: 2354bdea6d66
Create Date: 2026-10-15 19:54:25.452982

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cb4cc5211b23'
down_revision = '2354bdea6d66'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'socios',
        sa.Column('foto_actualizada_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column('socios', 'foto_actualizada_at')
