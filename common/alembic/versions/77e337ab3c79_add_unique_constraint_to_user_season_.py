"""Add unique constraint to user_season_pass

Revision ID: 77e337ab3c79
Revises: 993ab160a6fc
Create Date: 2023-11-24 02:50:48.577537

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '77e337ab3c79'
down_revision: Union[str, None] = '993ab160a6fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_unique_constraint('user_season_pass_unique', 'user_season_pass', ['planet_id', 'season_pass_id', 'avatar_addr'])
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('user_season_pass_unique', 'user_season_pass', type_='unique')
    # ### end Alembic commands ###