from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim, UserSeasonPass
from shared.utils.season_pass import get_pass
from sqlalchemy import desc, func, select

from app.celery import send_to_worker
from app.dependencies import session
from app.utils import verify_token

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


class SeasonPassResponse(BaseModel):
    id: int
    pass_type: str
    season_index: int
    start_timestamp: Optional[datetime]
    end_timestamp: Optional[datetime]
    reward_list: List[dict]
    instant_exp: int
    created_at: datetime
    updated_at: Optional[datetime]


class PaginatedSeasonPassResponse(BaseModel):
    total: int
    items: List[SeasonPassResponse]


class PremiumUserResponse(BaseModel):
    id: int
    planet_id: str
    agent_addr: str
    avatar_addr: str
    season_pass_id: int
    is_premium: bool
    is_premium_plus: bool
    exp: int
    level: int
    last_normal_claim: int
    last_premium_claim: int
    created_at: datetime
    updated_at: Optional[datetime]
    season_info: dict


class PaginatedPremiumUserResponse(BaseModel):
    total: int
    items: List[PremiumUserResponse]


router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(verify_token), Security(security)],  # 모든 admin 엔드포인트에 인증 필요
)


@router.get("/seasons", response_model=PaginatedSeasonPassResponse)
def get_seasons(
    pass_type: Optional[PassType] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sess=Depends(session),
):
    """모든 시즌 패스 목록을 조회합니다.

    Args:
        pass_type: 특정 패스 타입의 시즌만 조회
        limit: 한 페이지당 반환할 항목 수 (기본값: 20, 최대: 100)
        offset: 시작 위치 (기본값: 0)
    """
    base_query = select(SeasonPass).order_by(desc(SeasonPass.start_timestamp))

    if pass_type:
        base_query = base_query.where(SeasonPass.pass_type == pass_type)

    total_count = sess.scalar(select(func.count()).select_from(base_query.subquery()))

    seasons = sess.scalars(base_query.offset(offset).limit(limit)).all()

    items = [
        SeasonPassResponse(
            id=season.id,
            pass_type=season.pass_type.value,
            season_index=season.season_index,
            start_timestamp=season.start_timestamp,
            end_timestamp=season.end_timestamp,
            reward_list=season.reward_list,
            instant_exp=season.instant_exp,
            created_at=season.created_at,
            updated_at=season.updated_at,
        )
        for season in seasons
    ]

    return PaginatedSeasonPassResponse(total=total_count, items=items)


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


@router.get("/premium-users", response_model=PaginatedPremiumUserResponse)
def get_premium_users(
    pass_type: Optional[PassType] = None,
    season_index: Optional[int] = None,
    planet_id: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sess=Depends(session),
):
    """프리미엄 사용자 목록을 조회합니다.

    Args:
        pass_type: 특정 패스 타입의 사용자만 조회
        season_index: 특정 시즌의 사용자만 조회
        planet_id: 특정 플래닛의 사용자만 조회
        limit: 한 페이지당 반환할 항목 수 (기본값: 20, 최대: 100)
        offset: 시작 위치 (기본값: 0)
    """
    base_query = select(UserSeasonPass).where(UserSeasonPass.is_premium == True)

    if pass_type and season_index:
        target_season = get_pass(sess, pass_type, season_index)
        if target_season:
            base_query = base_query.where(UserSeasonPass.season_pass_id == target_season.id)
    elif pass_type:
        season_query = select(SeasonPass.id).where(SeasonPass.pass_type == pass_type)
        if season_index:
            season_query = season_query.where(SeasonPass.season_index == season_index)
        season_ids = sess.scalars(season_query).fetchall()
        if season_ids:
            base_query = base_query.where(UserSeasonPass.season_pass_id.in_(season_ids))

    if planet_id:
        planet_id_bytes = bytes(planet_id, "utf-8")
        base_query = base_query.where(UserSeasonPass.planet_id == planet_id_bytes)

    total_count = sess.scalar(select(func.count()).select_from(base_query.subquery()))

    users = sess.scalars(base_query.offset(offset).limit(limit)).all()

    items = []
    for user in users:
        season_pass = sess.get(SeasonPass, user.season_pass_id)
        season_info = {
            "id": season_pass.id,
            "pass_type": season_pass.pass_type.value,
            "season_index": season_pass.season_index,
            "start_timestamp": season_pass.start_timestamp,
            "end_timestamp": season_pass.end_timestamp,
        } if season_pass else {}

        items.append(
            PremiumUserResponse(
                id=user.id,
                planet_id=user.planet_id.decode() if isinstance(user.planet_id, bytes) else str(user.planet_id),
                agent_addr=user.agent_addr,
                avatar_addr=user.avatar_addr,
                season_pass_id=user.season_pass_id,
                is_premium=user.is_premium,
                is_premium_plus=user.is_premium_plus,
                exp=user.exp,
                level=user.level,
                last_normal_claim=user.last_normal_claim,
                last_premium_claim=user.last_premium_claim,
                created_at=user.created_at,
                updated_at=user.updated_at,
                season_info=season_info,
            )
        )

    return PaginatedPremiumUserResponse(total=total_count, items=items)


@router.post("/retry-stage")
def trigger_retry_stage():
    """스테이징 실패한 트랜잭션들을 재시도하는 태스크를 트리거합니다."""
    task_id = send_to_worker("season_pass.process_retry_stage", message={})
    return {"task_id": task_id, "status": "Task triggered successfully"}
