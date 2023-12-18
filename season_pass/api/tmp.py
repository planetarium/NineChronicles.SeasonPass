from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from common.models.season_pass import Level, SeasonPass
from common.models.user import UserSeasonPass
from common.utils.season_pass import get_current_season
from season_pass.dependencies import session
from season_pass.exceptions import UserNotFoundError, SeasonNotFoundError
from season_pass.schemas.season_pass import SeasonPassSchema
from season_pass.schemas.tmp import (PremiumRequestSchema, LevelRequestSchema, RegisterRequestSchema,
                                     SeasonChangeRequestSchema, )
from season_pass.schemas.user import UserSeasonPassSchema

router = APIRouter(
    prefix="/tmp",
    tags=["Temp"],
)


@router.post("/register", response_model=UserSeasonPassSchema)
def register_user(request: RegisterRequestSchema, sess=Depends(session)):
    current_season = get_current_season(sess)
    new_data = UserSeasonPass(
        planet_id=request.planet_id,
        agent_addr=request.agent_addr,
        avatar_addr=request.avatar_addr,
        season_pass_id=current_season.id,
    )
    sess.add(new_data)
    sess.commit()
    sess.refresh(new_data)
    return new_data


@router.post("/premium", response_model=UserSeasonPassSchema)
def set_premium(request: PremiumRequestSchema, sess=Depends(session)):
    current_season = get_current_season(sess)
    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(UserSeasonPass.avatar_addr == request.avatar_addr,
               UserSeasonPass.season_pass_id == current_season.id)
    )
    if not target_user:
        raise UserNotFoundError(f"User {request.avatar_addr} not found. Register first.")

    target_user.is_premium = request.is_premium
    target_user.is_premium_plus = request.is_premium_plus
    if target_user.is_premium_plus:
        target_user.is_premium = True
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/level", response_model=UserSeasonPassSchema)
def set_level(request: LevelRequestSchema, sess=Depends(session)):
    current_season = get_current_season(sess)
    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(UserSeasonPass.avatar_addr == request.avatar_addr,
               UserSeasonPass.season_pass_id == current_season.id)
    )
    if not target_user:
        raise UserNotFoundError(f"User {request.avatar_addr} not found. Register first.")

    if request.level:
        target_level = sess.scalar(select(Level).where(Level.level == request.level))
    elif request.exp:
        target_level = sess.scalar(select(Level).where(Level.exp <= request.exp).order_by(desc(Level.level)))
    else:
        raise Exception(f"Either level or exp must be provided.")
    if not target_level:
        raise Exception("Cannot find target level from provided data. Try again with new value.")

    target_user.exp = request.exp or target_level.exp
    target_user.level = target_level.level
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/change-season", response_model=SeasonPassSchema)
def change_season(request: SeasonChangeRequestSchema, sess: Session = Depends(session)):
    print(request)
    target_season = sess.scalar(select(SeasonPass).where(SeasonPass.id == request.season_id))
    if not target_season:
        raise SeasonNotFoundError(f"Season {request.season_id} not found")
    next_season = sess.scalar(select(SeasonPass).where(SeasonPass.id == request.season_id + 1))
    if not next_season:
        raise SeasonNotFoundError(f"Next season (Season ID {request.season_id + 1}) not found")

    target_season.end_timestamp = request.timestamp
    next_season.start_timestamp = request.timestamp
    sess.add(target_season)
    sess.add(next_season)
    sess.commit()
    sess.refresh(target_season)
    return SeasonPassSchema(
        id=target_season.id,
        start_date=target_season.start_date,
        end_date=target_season.end_date,
        start_timestamp=target_season.start_timestamp,
        end_timestamp=target_season.end_timestamp,
        reward_list=[
            {
                "level": reward["level"],
                "normal": {
                    "item": [
                        {
                            "id": x["ticker"].split("_")[-1],
                            "amount": x["amount"]
                        }
                        for x in reward["normal"] if x["ticker"].startswith("Item_")
                    ],
                    "currency": [
                        {
                            "ticker": x["ticker"].split("__")[-1],
                            "amount": x["amount"]
                        }
                        for x in reward["normal"] if x["ticker"].startswith("FAV__")
                    ]
                },
                "premium": {
                    "item": [
                        {
                            "id": x["ticker"].split("_")[-1],
                            "amount": x["amount"]
                        }
                        for x in reward["premium"] if x["ticker"].startswith("Item_")
                    ],
                    "currency": [
                        {
                            "ticker": x["ticker"].split("__")[-1],
                            "amount": x["amount"]
                        }
                        for x in reward["premium"] if x["ticker"].startswith("FAV__")
                    ]
                }
            }
            for reward in target_season.reward_list
        ]
    )
