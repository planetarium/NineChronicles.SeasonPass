"""Block identifies by both planet_id and index

Revision ID: 8f846cbfffec
Revises: 1eeafab14cd5
Create Date: 2023-11-10 16:22:45.631872

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f846cbfffec"
down_revision: Union[str, None] = "1eeafab14cd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.execute("ALTER TABLE block ADD COLUMN id SERIAL")
    op.execute("UPDATE block SET id=DEFAULT")
    op.alter_column("block", "id", nullable=False)

    op.execute("ALTER TABLE block ALTER COLUMN index DROP DEFAULT ")
    op.execute("DROP SEQUENCE block_index_seq")

    op.drop_constraint("block_pkey", "block")
    op.execute("ALTER TABLE block ADD PRIMARY KEY (id)")

    op.drop_index("ix_block_index", table_name="block")
    op.create_index(
        "idx_block_planet_index", "block", ["planet_id", "index"], unique=False
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("idx_block_planet_index", table_name="block")
    op.create_index("ix_block_index", "block", ["index"], unique=False)
    op.drop_constraint("block_pkey", "block")
    op.execute("ALTER TABLE block ADD PRIMARY KEY (index)")
    op.execute("CREATE SEQUENCE block_index_seq")
    op.drop_column("block", "id")
    # ### end Alembic commands ###
