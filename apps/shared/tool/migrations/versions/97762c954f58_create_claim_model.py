"""Create claim model

Revision ID: 97762c954f58
Revises: 040e3c091937
Create Date: 2023-10-25 00:29:46.528630

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "97762c954f58"
down_revision: Union[str, None] = "040e3c091937"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TX_STATUS_ENUM = postgresql.ENUM(
    *(
        "CREATED",
        "STAGED",
        "SUCCESS",
        "FAILURE",
        "INVALID",
        "NOT_FOUND",
        "FAIL_TO_CREATE",
        "UNKNOWN",
    ),
    name="txstatus",
    create_type=False
)


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    TX_STATUS_ENUM.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "claim",
        sa.Column("uuid", sa.Text(), nullable=False),
        sa.Column("agent_addr", sa.Text(), nullable=False),
        sa.Column("avatar_addr", sa.Text(), nullable=False),
        sa.Column(
            "reward_list", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("nonce", sa.Integer(), nullable=True),
        sa.Column("tx", sa.Text(), nullable=True),
        sa.Column("tx_id", sa.Text(), nullable=True),
        sa.Column("tx_status", TX_STATUS_ENUM, nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce"),
    )
    op.create_index(op.f("ix_claim_uuid"), "claim", ["uuid"], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_claim_uuid"), table_name="claim")
    op.drop_table("claim")
    TX_STATUS_ENUM.drop(op.get_bind(), checkfirst=False)
    # ### end Alembic commands ###
