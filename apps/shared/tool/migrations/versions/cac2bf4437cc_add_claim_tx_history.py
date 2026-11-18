"""Add claim_tx_history (재서명 B2)

claim 에서 같은 nonce 로 다시 서명할 때 교체된 옛 tx 를 남긴다. tx 추적기는 옛 tx 가 먼저
블록에 들어갔는지 확인하고, consumer 는 기록이 있는 claim 에 새 nonce 를 주지 않는다.

- 앱(워커·추적기)과 **같은 역할로** 실행한다. 다른 역할로 실행했다면 앱 역할에
  GRANT SELECT, INSERT, UPDATE ON claim_tx_history; GRANT USAGE ON SEQUENCE claim_tx_history_id_seq.
  권한이 없으면 앱은 재서명·기록 확정만 끄고 지급은 그대로 한다(shared/utils/claim_tx_history.py).
- lock_timeout 5초: FK 를 만드는 CREATE TABLE 은 claim 에 SHARE ROW EXCLUSIVE 를 잡는다. 오래 열린
  쓰기 트랜잭션이 claim 을 쥐고 있으면 그걸 기다리는 동안 뒤에 온 API 의 INSERT·UPDATE 까지 줄을
  선다. 5초를 넘기면 실패하고 사람이 다시 실행한다(그사이 지급이 멈추지 않게).
- downgrade 는 재서명이 한 번이라도 일어난 DB 에서 하지 않는다. 이 테이블이 "tx 가 나간 claim 의
  nonce 를 바꾸지 않는다"를 지키는 근거다. 되돌릴 때는 WORKER_/TRACKER_RESIGN_MODE 를 off 로 내린다.

Revision ID: cac2bf4437cc
Revises: bca8f726a5ee
Create Date: 2026-11-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cac2bf4437cc"
down_revision: Union[str, None] = "bca8f726a5ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # env.py 가 한 트랜잭션 안에서 돌리므로 LOCAL 이 이 마이그레이션 전체에 걸린다.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "claim_tx_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("planet_id", sa.LargeBinary(length=12), nullable=False),
        sa.Column("nonce", sa.Integer(), nullable=False),
        sa.Column("tx_id", sa.Text(), nullable=False),
        sa.Column("tx", sa.Text(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_nonce", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["claim.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tx_id"),
    )
    op.create_index(
        op.f("ix_claim_tx_history_claim_id"),
        "claim_tx_history",
        ["claim_id"],
        unique=False,
    )


def downgrade() -> None:
    # 재서명이 한 번이라도 일어난 DB 에서는 하지 않는다(위 docstring).
    op.drop_index(op.f("ix_claim_tx_history_claim_id"), table_name="claim_tx_history")
    op.drop_table("claim_tx_history")
