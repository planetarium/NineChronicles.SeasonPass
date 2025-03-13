"""Add Exp model

Revision ID: 9a4dafd6163e
Revises: f1944309b46c
Create Date: 2023-10-31 23:22:27.618397

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9a4dafd6163e"
down_revision: Union[str, None] = "f1944309b46c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTION_TYPE = sa.Enum(*("HAS", "SWEEP", "ARENA", "RAID"), name="actiontype")


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "exp",
        sa.Column("season_pass_id", sa.Integer(), nullable=False),
        sa.Column("action_type", ACTION_TYPE, nullable=False),
        sa.Column("exp", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["season_pass_id"],
            ["season_pass.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("exp")
    ACTION_TYPE.drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###
