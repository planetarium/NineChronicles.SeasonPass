"""Drop never-used secondary indexes on action_history / user_season_pass

Revision ID: 3b7e1c9d2f40
Revises: bca8f726a5ee
Create Date: 2026-10-08 00:00:00.000000

These four indexes had 0 index scans in production and no code path reads
them, yet every INSERT/UPDATE paid to maintain them:

- action_history.idx_season_avatar (season_id, avatar_addr)  ~5.2GB
- action_history.ix_action_history_action (action)           ~1.7GB
  action_history is a write-only audit log; nothing queries it.
- user_season_pass.ix_user_season_pass_agent_addr (agent_addr)
- user_season_pass.ix_user_season_pass_avatar_addr (avatar_addr)
  Every lookup filters planet_id + season_pass_id + avatar_addr, which is
  covered by user_season_pass_unique. avatar_season is kept.

Production already dropped them by hand (DROP INDEX CONCURRENTLY,
2026-10-08), so upgrade() is IF EXISTS and is a no-op there.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3b7e1c9d2f40"
down_revision: Union[str, None] = "bca8f726a5ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_season_avatar")
    op.execute("DROP INDEX IF EXISTS ix_action_history_action")
    op.execute("DROP INDEX IF EXISTS ix_user_season_pass_agent_addr")
    op.execute("DROP INDEX IF EXISTS ix_user_season_pass_avatar_addr")


def downgrade() -> None:
    # Slow on large tables (action_history is tens of GB) and blocks writes
    # while building. Prefer CREATE INDEX CONCURRENTLY by hand if needed.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_season_pass_avatar_addr "
        "ON user_season_pass (avatar_addr)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_season_pass_agent_addr "
        "ON user_season_pass (agent_addr)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_action_history_action "
        "ON action_history (action)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_season_avatar "
        "ON action_history (season_id, avatar_addr)"
    )
