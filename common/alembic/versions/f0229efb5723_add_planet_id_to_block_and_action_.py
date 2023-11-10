"""Add planet_id to block and action_history

Revision ID: f0229efb5723
Revises: b0b9cb232f67
Create Date: 2023-11-10 02:37:06.659523

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0229efb5723'
down_revision: Union[str, None] = 'b0b9cb232f67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('action_history', sa.Column('planet_id', sa.LargeBinary(length=12), nullable=True))
    op.execute("UPDATE action_history SET planet_id = '0x000000000000'::bytea")
    op.alter_column("action_history", "planet_id", nullable=False)
    op.add_column('block', sa.Column('planet_id', sa.LargeBinary(length=12), nullable=True))
    op.execute("UPDATE block SET planet_id = '0x000000000000'::bytea")
    op.alter_column("block", "planet_id", nullable=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('block', 'planet_id')
    op.drop_column('action_history', 'planet_id')
    # ### end Alembic commands ###