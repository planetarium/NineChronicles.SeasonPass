"""Add partial index for unsettled claims (invalid-claim / claim guard)

Revision ID: 61d0dd969e60
Revises: bca8f726a5ee
Create Date: 2026-06-29 01:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "61d0dd969e60"
down_revision: Union[str, None] = "bca8f726a5ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: mainnet already has this index (created out-of-band via
# CREATE INDEX CONCURRENTLY), so `if_not_exists` makes the migration a no-op
# there. The `claim` table is ~1.7GB / 1M+ rows; on a large fresh database
# prefer creating this CONCURRENTLY out-of-band first, then running the
# migration (a plain CREATE INDEX takes a write lock for the build).
INDEX_NAME = "idx_claim_unsettled"
WHERE = "reward_list <> '[]' AND (tx_status <> 'SUCCESS' OR tx_status IS NULL)"


def upgrade() -> None:
    # Serves /api/invalid-claim and the claim-guard count in user.claim_reward.
    # Without it those do a full seq scan of the whole claim table on every
    # call, which balloons to a request timeout under load. The partial index
    # only covers unsettled (non-SUCCESS, reward-bearing) claims, so it stays
    # tiny and turns the scan into an index-only scan.
    op.create_index(
        INDEX_NAME,
        "claim",
        ["created_at"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text(WHERE),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="claim", if_exists=True)
