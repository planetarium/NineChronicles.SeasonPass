"""Add unique constraint for block and index

Revision ID: 08e20d0fcb90
Revises: 8f846cbfffec
Create Date: 2023-11-10 19:04:18.659843

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '08e20d0fcb90'
down_revision: Union[str, None] = '8f846cbfffec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_unique_constraint('block_by_planet_unique', 'block', ['planet_id', 'index'])
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('block_by_planet_unique', 'block', type_='unique')
    # ### end Alembic commands ###
