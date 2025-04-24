from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.dependencies import session
from app.utils import verify_token
from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from shared.enums import TxStatus
from shared.models.user import Claim
from sqlalchemy import desc, func, select

security = HTTPBearer()


class ClaimResponse(BaseModel):
    uuid: str
    avatar_addr: str
    planet_id: str
    tx_id: Optional[str]
    tx_status: Optional[TxStatus]
    nonce: Optional[int]
    reward_list: List[dict]
    created_at: datetime
    updated_at: Optional[datetime]


class PaginatedClaimResponse(BaseModel):
    total: int
    items: List[ClaimResponse]


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_token), Security(security)],  # 모든 admin 엔드포인트에 인증 필요
)


@router.get("/claims", response_model=PaginatedClaimResponse)
def get_claims(
    avatar_addr: Optional[str] = None,
    status: Optional[TxStatus] = None,
    days: Optional[int] = Query(default=7, ge=1, le=30),
    limit: int = Query(default=20, ge=1, le=100),  # 한 페이지당 기본 20개, 최대 100개
    offset: int = Query(default=0, ge=0),  # 시작 위치
    sess=Depends(session),
):
    """Claim 정보를 조회합니다.

    Args:
        avatar_addr: 특정 사용자의 Claim만 조회
        status: 특정 상태의 Claim만 조회 (SUCCESS, FAILURE 등)
        days: 최근 몇일간의 데이터를 조회할지 (기본값: 7일, 최대 30일)
        limit: 한 페이지당 반환할 항목 수 (기본값: 20, 최대: 100)
        offset: 시작 위치 (기본값: 0)
    """
    # 기본 쿼리 생성
    base_query = select(Claim).order_by(desc(Claim.created_at))

    # 날짜 필터링
    start_date = datetime.now(tz=timezone.utc) - timedelta(days=days)
    base_query = base_query.where(Claim.created_at >= start_date)

    # 사용자 필터링
    if avatar_addr:
        base_query = base_query.where(Claim.avatar_addr == avatar_addr)

    # 상태 필터링
    if status:
        base_query = base_query.where(Claim.tx_status == status)

    # 전체 결과 수 계산
    total_count = sess.scalar(select(func.count()).select_from(base_query.subquery()))

    # 페이지네이션 적용
    claims = sess.scalars(base_query.offset(offset).limit(limit)).all()

    # 응답 데이터 생성
    items = [
        ClaimResponse(
            uuid=claim.uuid,
            avatar_addr=claim.avatar_addr,
            planet_id=claim.planet_id,
            tx_id=claim.tx_id,
            tx_status=claim.tx_status,
            nonce=claim.nonce,
            reward_list=claim.reward_list,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        )
        for claim in claims
    ]

    return PaginatedClaimResponse(total=total_count, items=items)
