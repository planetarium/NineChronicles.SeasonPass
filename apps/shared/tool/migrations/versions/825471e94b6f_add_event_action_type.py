"""Add Event action type

Revision ID: 825471e94b6f
Revises: 77e337ab3c79
Create Date: 2023-12-18 22:25:18.656938

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "825471e94b6f"
down_revision: Union[str, None] = "77e337ab3c79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

old_enum = ("HAS", "SWEEP", "ARENA", "RAID")
new_enum = sorted(old_enum + ("EVENT",))

old_type = sa.Enum(*old_enum, name="actiontype")
new_type = sa.Enum(*new_enum, name="actiontype")
tmp_type = sa.Enum(*new_enum, name="_actiontype")


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    tmp_type.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE _actiontype USING action_type::text::_actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE _actiontype USING action::text::_actiontype"
    )
    old_type.drop(op.get_bind(), checkfirst=False)
    new_type.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE actiontype USING action_type::text::actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE actiontype USING action::text::actiontype"
    )
    tmp_type.drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.execute("UPDATE action_history SET action='HAS' where action='EVENT'")
    tmp_type.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE _actiontype USING action_type::text::_actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE _actiontype USING action::text::_actiontype"
    )
    new_type.drop(op.get_bind(), checkfirst=False)
    old_type.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE exp ALTER COLUMN action_type TYPE actiontype USING action_type::text::actiontype"
    )
    op.execute(
        "ALTER TABLE action_history ALTER COLUMN action TYPE actiontype USING action::text::actiontype"
    )
    tmp_type.drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###
