"""Add missing constraint update

Revision ID: d7d731964ef7
Revises: a46e378a9cfa
Create Date: 2024-11-20 23:38:45.551680

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7d731964ef7"
down_revision: Union[str, None] = "a46e378a9cfa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint("block_by_planet_unique", "block", type_="unique")
    op.create_unique_constraint(
        "block_by_pass_planet_unique", "block", ["planet_id", "pass_type", "index"]
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint("block_by_pass_planet_unique", "block", type_="unique")
    op.create_unique_constraint(
        "block_by_planet_unique", "block", ["planet_id", "index"]
    )
    # ### end Alembic commands ###
