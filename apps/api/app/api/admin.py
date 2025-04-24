from datetime import datetime, timedelta, timezone
from typing import Optional

from app.dependencies import session
from app.utils import verify_token
from fastapi import APIRouter, Depends, Query
from shared.enums import TxStatus
from shared.models.user import Claim
from sqlalchemy import desc, select

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_token)],  # 모든 admin 엔드포인트에 인증 필요
)


@router.get("/claims")
def get_claims(
    avatar_addr: Optional[str] = None,
    status: Optional[TxStatus] = None,
    days: Optional[int] = Query(default=7, ge=1, le=30),
    sess=Depends(session),
):
    """Claim 정보를 조회합니다.

    Args:
        avatar_addr: 특정 사용자의 Claim만 조회
        status: 특정 상태의 Claim만 조회 (SUCCESS, FAILURE 등)
        days: 최근 몇일간의 데이터를 조회할지 (기본값: 7일, 최대 30일)
    """
    query = select(Claim).order_by(desc(Claim.created_at))

    # 날짜 필터링
    start_date = datetime.now(tz=timezone.utc) - timedelta(days=days)
    query = query.where(Claim.created_at >= start_date)

    # 사용자 필터링
    if avatar_addr:
        query = query.where(Claim.avatar_addr == avatar_addr)

    # 상태 필터링
    if status:
        query = query.where(Claim.tx_status == status)

    claims = sess.scalars(query).all()

    return [
        {
            "uuid": claim.uuid,
            "avatar_addr": claim.avatar_addr,
            "planet_id": claim.planet_id,
            "tx_id": claim.tx_id,
            "tx_status": claim.tx_status,
            "nonce": claim.nonce,
            "reward_list": claim.reward_list,
            "created_at": claim.created_at,
            "updated_at": claim.updated_at,
        }
        for claim in claims
    ]
