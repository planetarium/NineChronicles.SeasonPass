"""Add adventure boss history

Revision ID: a46e378a9cfa
Revises: 135741a7ed0d
Create Date: 2024-11-04 22:57:39.014284

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a46e378a9cfa"
down_revision: Union[str, None] = "135741a7ed0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

old_action_enum = postgresql.ENUM(
    *("HAS", "SWEEP", "ARENA", "RAID", "EVENT"), name="actiontype"
)
new_action_enum = postgresql.ENUM(
    *("HAS", "SWEEP", "ARENA", "RAID", "EVENT", "WANTED", "CHALLENGE", "RUSH"),
    name="actiontype"
)
tmp_action_enum = postgresql.ENUM(
    *("HAS", "SWEEP", "ARENA", "RAID", "EVENT", "WANTED", "CHALLENGE", "RUSH"),
    name="_actiontype"
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    tmp_action_enum.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE _actiontype USING action_type::text::_actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE _actiontype USING action::text::_actiontype"
    )
    old_action_enum.drop(op.get_bind(), checkfirst=False)
    new_action_enum.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE actiontype USING action_type::text::actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE actiontype USING action::text::actiontype"
    )
    tmp_action_enum.drop(op.get_bind(), checkfirst=False)

    op.create_table(
        "adventure_boss_history",
        sa.Column("planet_id", sa.LargeBinary(length=12), nullable=False),
        sa.Column("agent_addr", sa.Text(), nullable=False),
        sa.Column("avatar_addr", sa.Text(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("floor", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_adventure_boss_floor_history",
        "adventure_boss_history",
        ["season", "planet_id", "avatar_addr"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(
        "idx_adventure_boss_floor_history", table_name="adventure_boss_history"
    )
    op.drop_table("adventure_boss_history")

    # New actions goes to HAS, the default action: But this is not good
    op.execute(
        "UPDATE action_history SET action='HAS' WHERE action IN ('WANTED', 'CHALLENGE', 'RUSH')"
    )
    op.execute("DELETE FROM exp WHERE action_type in ('WANTED', 'CHALLENGE', 'RUSH')")
    tmp_action_enum.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE _actiontype USING action_type::text::_actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE _actiontype USING action::text::_actiontype"
    )
    new_action_enum.drop(op.get_bind(), checkfirst=False)
    old_action_enum.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE actiontype USING action_type::text::actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE actiontype USING action::text::actiontype"
    )
    tmp_action_enum.drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###
