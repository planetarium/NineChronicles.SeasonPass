"""Add pass_type to Block table

Revision ID: 135741a7ed0d
Revises: 665aa93461c4
Create Date: 2024-11-01 17:13:32.810226

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '135741a7ed0d'
down_revision: Union[str, None] = '665aa93461c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('block', sa.Column('pass_type', sa.Enum('COURAGE_PASS', 'ADVENTURE_BOSS_PASS', 'WORLD_CLEAR_PASS', name='passtype'), nullable=True))
    op.execute("UPDATE block set pass_type='COURAGE_PASS'")  # all the prev. block data are for courage pass
    op.drop_index('idx_block_planet_index', table_name='block')
    op.create_index('idx_block_planet_pass_type_index', 'block', ['planet_id', 'pass_type', 'index'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('idx_block_planet_pass_type_index', table_name='block')
    op.create_index('idx_block_planet_index', 'block', ['planet_id', 'index'], unique=False)
    op.drop_column('block', 'pass_type')
    # ### end Alembic commands ###
