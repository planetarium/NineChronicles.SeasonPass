from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.celery import send_to_worker
from app.dependencies import session
from app.schemas.admin import BurnAssetRequest, BurnAssetResponse
from app.utils import verify_token
from fastapi import APIRouter, Depends, HTTPException, Query, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from shared.enums import ActionType, PassType, TxStatus
from shared.models.season_pass import Exp, SeasonPass
from shared.models.user import Claim, UserSeasonPass
from shared.utils.season_pass import get_pass
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


# 새로운 CRUD 스키마들
class CreateExpSchema(BaseModel):
    action_type: ActionType
    exp: int


class CreateSeasonPassSchema(BaseModel):
    pass_type: PassType
    season_index: int
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: List[dict] = []
    instant_exp: int = 0
    exp_list: List[CreateExpSchema] = []


class UpdateSeasonPassSchema(BaseModel):
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: Optional[List[dict]] = None
    instant_exp: Optional[int] = None
    exp_list: Optional[List[CreateExpSchema]] = None


class SeasonPassDetailSchema(BaseModel):
    id: int
    pass_type: PassType
    season_index: int
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: List[dict] = []
    repeat_last_reward: bool = True
    instant_exp: int = 0
    exp_list: List[dict] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


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
            base_query = base_query.where(
                UserSeasonPass.season_pass_id == target_season.id
            )
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
        season_info = (
            {
                "id": season_pass.id,
                "pass_type": season_pass.pass_type.value,
                "season_index": season_pass.season_index,
                "start_timestamp": season_pass.start_timestamp,
                "end_timestamp": season_pass.end_timestamp,
            }
            if season_pass
            else {}
        )

        items.append(
            PremiumUserResponse(
                id=user.id,
                planet_id=user.planet_id.decode()
                if isinstance(user.planet_id, bytes)
                else str(user.planet_id),
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


# 새로운 시즌패스 CRUD 엔드포인트들
@router.get("/season-passes", response_model=List[SeasonPassDetailSchema])
def get_season_passes(
    pass_type: Optional[PassType] = None,
    season_index: Optional[int] = None,
    sess=Depends(session),
):
    """시즌패스 목록을 조회합니다.

    Args:
        pass_type: 특정 패스 타입만 조회
        season_index: 특정 시즌 인덱스만 조회
    """
    query = select(SeasonPass).order_by(desc(SeasonPass.id))

    if pass_type:
        query = query.where(SeasonPass.pass_type == pass_type)

    if season_index is not None:
        query = query.where(SeasonPass.season_index == season_index)

    season_passes = sess.scalars(query).all()

    result = []
    for season_pass in season_passes:
        # exp_list를 딕셔너리로 변환
        exp_list = [
            {
                "id": exp.id,
                "season_pass_id": exp.season_pass_id,
                "action_type": exp.action_type.value,
                "exp": exp.exp,
                "created_at": exp.created_at,
                "updated_at": exp.updated_at,
            }
            for exp in season_pass.exp_list
        ]

        result.append(
            SeasonPassDetailSchema(
                id=season_pass.id,
                pass_type=season_pass.pass_type,
                season_index=season_pass.season_index,
                start_date=season_pass.start_date,
                end_date=season_pass.end_date,
                start_timestamp=season_pass.start_timestamp,
                end_timestamp=season_pass.end_timestamp,
                reward_list=season_pass.reward_list,
                repeat_last_reward=season_pass.pass_type != PassType.WORLD_CLEAR_PASS,
                instant_exp=season_pass.instant_exp,
                exp_list=exp_list,
                created_at=season_pass.created_at,
                updated_at=season_pass.updated_at,
            )
        )

    return result


@router.get("/season-passes/{season_pass_id}", response_model=SeasonPassDetailSchema)
def get_season_pass(season_pass_id: int, sess=Depends(session)):
    """특정 시즌패스를 조회합니다."""
    season_pass = sess.get(SeasonPass, season_pass_id)
    if not season_pass:
        raise HTTPException(status_code=404, detail="Season pass not found")

    # exp_list를 딕셔너리로 변환
    exp_list = [
        {
            "id": exp.id,
            "season_pass_id": exp.season_pass_id,
            "action_type": exp.action_type.value,
            "exp": exp.exp,
            "created_at": exp.created_at,
            "updated_at": exp.updated_at,
        }
        for exp in season_pass.exp_list
    ]

    return SeasonPassDetailSchema(
        id=season_pass.id,
        pass_type=season_pass.pass_type,
        season_index=season_pass.season_index,
        start_date=season_pass.start_date,
        end_date=season_pass.end_date,
        start_timestamp=season_pass.start_timestamp,
        end_timestamp=season_pass.end_timestamp,
        reward_list=season_pass.reward_list,
        repeat_last_reward=season_pass.pass_type != PassType.WORLD_CLEAR_PASS,
        instant_exp=season_pass.instant_exp,
        exp_list=exp_list,
        created_at=season_pass.created_at,
        updated_at=season_pass.updated_at,
    )


@router.post("/season-passes", response_model=SeasonPassDetailSchema)
def create_season_pass(
    season_pass_data: CreateSeasonPassSchema,
    sess=Depends(session),
):
    """새로운 시즌패스를 생성합니다."""
    # 기존 시즌패스가 있는지 확인 (pass_type + season_index 조합)
    existing = sess.scalar(
        select(SeasonPass).where(
            SeasonPass.pass_type == season_pass_data.pass_type,
            SeasonPass.season_index == season_pass_data.season_index,
        )
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Season pass already exists for {season_pass_data.pass_type}:{season_pass_data.season_index}",
        )

    try:
        # 시즌패스 생성
        season_pass = SeasonPass(
            pass_type=season_pass_data.pass_type,
            season_index=season_pass_data.season_index,
            start_timestamp=season_pass_data.start_timestamp,
            end_timestamp=season_pass_data.end_timestamp,
            reward_list=season_pass_data.reward_list,
            instant_exp=season_pass_data.instant_exp,
        )
        sess.add(season_pass)
        sess.flush()  # ID 생성을 위해 flush

        # Exp 데이터 생성
        for exp_data in season_pass_data.exp_list:
            exp = Exp(
                season_pass_id=season_pass.id,
                action_type=exp_data.action_type,
                exp=exp_data.exp,
            )
            sess.add(exp)

        sess.commit()
        sess.refresh(season_pass)
    except Exception:
        sess.rollback()
        raise

    # exp_list를 딕셔너리로 변환
    exp_list = [
        {
            "id": exp.id,
            "season_pass_id": exp.season_pass_id,
            "action_type": exp.action_type.value,
            "exp": exp.exp,
            "created_at": exp.created_at,
            "updated_at": exp.updated_at,
        }
        for exp in season_pass.exp_list
    ]

    return SeasonPassDetailSchema(
        id=season_pass.id,
        pass_type=season_pass.pass_type,
        season_index=season_pass.season_index,
        start_timestamp=season_pass.start_timestamp,
        end_timestamp=season_pass.end_timestamp,
        reward_list=season_pass.reward_list,
        instant_exp=season_pass.instant_exp,
        exp_list=exp_list,
        created_at=season_pass.created_at,
        updated_at=season_pass.updated_at,
    )


@router.put("/season-passes/{season_pass_id}", response_model=SeasonPassDetailSchema)
def update_season_pass(
    season_pass_id: int,
    season_pass_data: CreateSeasonPassSchema,
    sess=Depends(session),
):
    """시즌패스를 수정합니다."""
    season_pass = sess.get(SeasonPass, season_pass_id)
    if not season_pass:
        raise HTTPException(status_code=404, detail="Season pass not found")

    try:
        # 시즌패스 정보 업데이트
        season_pass.pass_type = season_pass_data.pass_type
        season_pass.season_index = season_pass_data.season_index
        season_pass.start_timestamp = season_pass_data.start_timestamp
        season_pass.end_timestamp = season_pass_data.end_timestamp
        season_pass.reward_list = season_pass_data.reward_list
        season_pass.instant_exp = season_pass_data.instant_exp

        # 기존 Exp 데이터 삭제
        sess.query(Exp).where(Exp.season_pass_id == season_pass_id).delete()

        # Exp 데이터 생성
        for exp_data in season_pass_data.exp_list:
            exp = Exp(
                season_pass_id=season_pass_id,
                action_type=exp_data.action_type,
                exp=exp_data.exp,
            )
            sess.add(exp)

        sess.commit()
        sess.refresh(season_pass)
    except Exception:
        sess.rollback()
        raise

    # exp_list를 딕셔너리로 변환
    exp_list = [
        {
            "id": exp.id,
            "season_pass_id": exp.season_pass_id,
            "action_type": exp.action_type.value,
            "exp": exp.exp,
            "created_at": exp.created_at,
            "updated_at": exp.updated_at,
        }
        for exp in season_pass.exp_list
    ]

    return SeasonPassDetailSchema(
        id=season_pass.id,
        pass_type=season_pass.pass_type,
        season_index=season_pass.season_index,
        start_date=season_pass.start_date,
        end_date=season_pass.end_date,
        start_timestamp=season_pass.start_timestamp,
        end_timestamp=season_pass.end_timestamp,
        reward_list=season_pass.reward_list,
        repeat_last_reward=season_pass.pass_type != PassType.WORLD_CLEAR_PASS,
        instant_exp=season_pass.instant_exp,
        exp_list=exp_list,
        created_at=season_pass.created_at,
        updated_at=season_pass.updated_at,
    )


@router.delete("/season-passes/{season_pass_id}")
def delete_season_pass(season_pass_id: int, sess=Depends(session)):
    """시즌패스를 삭제합니다."""
    season_pass = sess.get(SeasonPass, season_pass_id)
    if not season_pass:
        raise HTTPException(status_code=404, detail="Season pass not found")

    try:
        # 관련 Exp 데이터도 함께 삭제
        sess.query(Exp).where(Exp.season_pass_id == season_pass_id).delete()
        sess.delete(season_pass)
        sess.commit()

        return {"message": "Season pass deleted successfully"}
    except Exception:
        sess.rollback()
        raise


@router.post("/burn-asset", response_model=BurnAssetResponse)
def burn_asset(burn_request: BurnAssetRequest):
    """어드민이 burn asset 액션을 서명하고 스테이징합니다.

    Args:
        burn_request: Burn asset 요청 데이터
            - ticker: 통화 티커 (예: "NCG", "CRYSTAL")
            - amount: 소각할 양
            - memo: 메모 (선택사항)
            - planet_id: 플래닛 ID (기본값: ODIN)
    """
    try:
        # Worker에 burn asset task 전송 (Account에서 주소 가져옴)
        task_id = send_to_worker(
            "season_pass.process_burn_asset",
            message={
                "ticker": burn_request.ticker,
                "amount": str(burn_request.amount),
                "memo": burn_request.memo,
                "planet_id": burn_request.planet_id,
            },
        )

        return BurnAssetResponse(
            task_id=task_id,
            status="success",
            message="Burn asset task triggered successfully",
        )

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger burn asset task: {str(e)}"
        )
