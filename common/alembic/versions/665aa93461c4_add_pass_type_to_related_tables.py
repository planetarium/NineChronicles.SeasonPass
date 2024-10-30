"""Add pass_type to related tables

Revision ID: 665aa93461c4
Revises: ac1e86a25be3
Create Date: 2024-10-28 22:27:40.820706

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '665aa93461c4'
down_revision: Union[str, None] = 'ac1e86a25be3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('exp', sa.Column('pass_type', sa.Enum('COURAGE_PASS', 'ADVENTURE_BOSS_PASS', 'WORLD_CLEAR_PASS', name='passtype'), nullable=True))
    op.execute("UPDATE exp SET pass_type='COURAGE_PASS'")
    op.alter_column("exp", "pass_type", nullable=False)
    op.drop_constraint('exp_season_pass_id_fkey', 'exp', type_='foreignkey')
    op.drop_column('exp', 'season_pass_id')

    op.add_column('level', sa.Column('pass_type', sa.Enum('COURAGE_PASS', 'ADVENTURE_BOSS_PASS', 'WORLD_CLEAR_PASS', name='passtype'), nullable=True))
    op.execute("UPDATE level SET pass_type='COURAGE_PASS'")
    op.alter_column("level", "pass_type", nullable=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('level', 'pass_type')
    op.add_column('exp', sa.Column('season_pass_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.execute("UPDATE exp SET season_pass_id=(id-1) / 5 + 1")
    op.create_foreign_key('exp_season_pass_id_fkey', 'exp', 'season_pass', ['season_pass_id'], ['id'])
    op.drop_column('exp', 'pass_type')
    # ### end Alembic commands ###