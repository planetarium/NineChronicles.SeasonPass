"""Create base models

Revision ID: 040e3c091937
Revises: 
Create Date: 2023-10-19 10:55:47.249499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '040e3c091937'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('level',
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('exp', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('season_pass',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('reward_list', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_season_pass_id'), 'season_pass', ['id'], unique=False)
    op.create_table('user_season_pass',
    sa.Column('agent_addr', sa.Text(), nullable=False),
    sa.Column('avatar_addr', sa.Text(), nullable=False),
    sa.Column('season_pass_id', sa.Integer(), nullable=False),
    sa.Column('is_premium', sa.Boolean(), nullable=False),
    sa.Column('exp', sa.Integer(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('last_normal_claim', sa.Integer(), nullable=False),
    sa.Column('last_premium_claim', sa.Integer(), nullable=False),
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['season_pass_id'], ['season_pass.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('avatar_season', 'user_season_pass', ['avatar_addr', 'season_pass_id'], unique=False)
    op.create_index(op.f('ix_user_season_pass_agent_addr'), 'user_season_pass', ['agent_addr'], unique=False)
    op.create_index(op.f('ix_user_season_pass_avatar_addr'), 'user_season_pass', ['avatar_addr'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_user_season_pass_avatar_addr'), table_name='user_season_pass')
    op.drop_index(op.f('ix_user_season_pass_agent_addr'), table_name='user_season_pass')
    op.drop_index('avatar_season', table_name='user_season_pass')
    op.drop_table('user_season_pass')
    op.drop_index(op.f('ix_season_pass_id'), table_name='season_pass')
    op.drop_table('season_pass')
    op.drop_table('level')
    # ### end Alembic commands ###
