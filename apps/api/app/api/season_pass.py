from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime
from shared.enums import PassType, PlanetID
from shared.models.season_pass import Level
from shared.utils.season_pass import get_pass
from sqlalchemy import select

from app.dependencies import session
from app.exceptions import InvalidSeasonError, SeasonNotFoundError
from app.schemas.season_pass import ExpInfoSchema, LevelInfoSchema, SeasonPassSchema

router = APIRouter(
    prefix="/season-pass",
    tags=["SeasonPass"],
)


#: `at` 의 미래 허용 폭 — 서버 간 시계 차이만 흡수한다.
AT_FUTURE_SKEW = timedelta(minutes=5)


@router.get("/current", response_model=SeasonPassSchema)
def current_season(
    planet_id: str,
    pass_type: PassType,
    at: Optional[AwareDatetime] = None,
    sess=Depends(session),
):
    """
    `at` 을 주면 그 시각에 진행 중이던 시즌을 돌려준다(기본: 지금).

    IAP 가 고정 SKU 결제를 **결제 시각** 기준 시즌에 귀속시킬 때 쓴다 — 경계 직전 결제가
    늦게 도착해도 다음 시즌으로 넘어가지 않는다. 시각은 offset 이 있어야 한다(없으면 422).

    미래 시각(`AT_FUTURE_SKEW` 초과)은 400 이다. 이 엔드포인트는 인증이 없어서, 받아 주면
    미리 등록해 둔 **공개 전 시즌**의 reward_list 가 노출된다(user.py 의 "not opened yet"
    가드와 같은 이유). 결제 시각은 늘 과거라 IAP 는 영향이 없다.
    """
    if at is not None and at > datetime.now(tz=timezone.utc) + AT_FUTURE_SKEW:
        raise InvalidSeasonError(f"`at` must not be in the future: {at.isoformat()}")
    planet_id = PlanetID(bytes(planet_id, "utf-8"))
    curr_season = get_pass(sess, pass_type, validate_current=True, at=at)
    if not curr_season:
        raise SeasonNotFoundError(
            f"No active season pass at {at.isoformat()}"
            if at
            else "No active season pass for today"
        )

    reward_coef = 1

    return SeasonPassSchema(
        id=curr_season.id,
        pass_type=curr_season.pass_type,
        season_index=curr_season.season_index,
        start_date=curr_season.start_date,
        end_date=curr_season.end_date,
        start_timestamp=curr_season.start_timestamp,
        end_timestamp=curr_season.end_timestamp,
        reward_list=[
            {
                "level": reward["level"],
                "normal": {
                    "item": [
                        {
                            "id": x["ticker"].split("_")[-1],
                            "amount": x["amount"] * reward_coef,
                        }
                        for x in reward["normal"]
                        if x["ticker"].startswith("Item_")
                    ],
                    "currency": [
                        {
                            "ticker": x["ticker"].split("__")[-1],
                            "amount": x["amount"] * reward_coef,
                        }
                        for x in reward["normal"]
                        if x["ticker"].startswith("FAV__")
                    ],
                },
                "premium": {
                    "item": [
                        {
                            "id": x["ticker"].split("_")[-1],
                            "amount": x["amount"] * reward_coef,
                        }
                        for x in reward["premium"]
                        if x["ticker"].startswith("Item_")
                    ],
                    "currency": [
                        {
                            "ticker": x["ticker"].split("__")[-1],
                            "amount": x["amount"] * reward_coef,
                        }
                        for x in reward["premium"]
                        if x["ticker"].startswith("FAV__")
                    ],
                },
            }
            for reward in curr_season.reward_list
        ],
        # World clear pass does not have repeat reward
        repeat_last_reward=curr_season.pass_type != PassType.WORLD_CLEAR_PASS,
    )


@router.get("/level", response_model=List[LevelInfoSchema])
def level_info(pass_type: PassType, sess=Depends(session)):
    return sess.scalars(
        select(Level).where(Level.pass_type == pass_type).order_by(Level.level)
    ).fetchall()


@router.get("/exp", response_model=List[ExpInfoSchema])
def exp_info(pass_type: PassType, season_index: int, sess=Depends(session)):
    current_pass = get_pass(
        sess, pass_type=pass_type, season_index=season_index, include_exp=True
    )
    return current_pass.exp_list
