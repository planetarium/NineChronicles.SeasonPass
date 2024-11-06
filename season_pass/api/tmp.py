from fastapi import APIRouter, Depends
from sqlalchemy import select, desc

from common.models.season_pass import Level
from common.models.user import UserSeasonPass
from common.utils.season_pass import get_pass
from season_pass.dependencies import session
from season_pass.exceptions import UserNotFoundError, SeasonNotFoundError
from season_pass.schemas.season_pass import SeasonPassSchema
from season_pass.schemas.tmp import (PremiumRequestSchema, RegisterRequestSchema, ExpRequestSchema,
                                     SeasonChangeRequestSchema,
                                     )
from season_pass.schemas.user import UserSeasonPassSchema

router = APIRouter(
    prefix="/tmp",
    tags=["Temp"],
)


@router.post("/register", response_model=UserSeasonPassSchema)
def register_user(request: RegisterRequestSchema, sess=Depends(session)):
    target_season = get_pass(sess, request.pass_type, request.season_index)
    if not target_season:
        raise SeasonNotFoundError(f"{request.pass_type}:{request.season_index} not found")
    prev_data = sess.scalar(select(UserSeasonPass).where(
        UserSeasonPass.planet_id == request.planet_id,
        UserSeasonPass.agent_addr == request.agent_addr,
        UserSeasonPass.avatar_addr == request.avatar_addr,
        UserSeasonPass.season_pass_id == target_season.id
    ))
    if prev_data:
        return prev_data

    new_data = UserSeasonPass(
        planet_id=request.planet_id,
        agent_addr=request.agent_addr,
        avatar_addr=request.avatar_addr,
        season_pass_id=target_season.id,
    )
    sess.add(new_data)
    sess.commit()
    sess.refresh(new_data)
    return new_data


@router.post("/premium", response_model=UserSeasonPassSchema)
def set_premium(request: PremiumRequestSchema, sess=Depends(session)):
    target_season = get_pass(sess, request.pass_type, season_index=request.season_index)
    if not target_season:
        raise SeasonNotFoundError(f"{request.pass_type}:{request.season_index} not found")
    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(UserSeasonPass.avatar_addr == request.avatar_addr,
               UserSeasonPass.season_pass_id == target_season.id)
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


@router.post("/add-exp", response_model=UserSeasonPassSchema)
def add_exp(request: ExpRequestSchema, sess=Depends(session)):
    target_season = get_pass(sess, request.pass_type, request.season_index, include_exp=True)
    if not target_season:
        raise SeasonNotFoundError(f"{request.pass_type}:{request.season_index} not found")
    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(UserSeasonPass.avatar_addr == request.avatar_addr,
               UserSeasonPass.season_pass_id == target_season.id)
    )
    if not target_user:
        raise UserNotFoundError(f"User {request.avatar_addr} not found. Register first.")
    target_user.exp += request.exp
    for lvl in sess.scalars(
            select(Level).where(Level.pass_type == request.pass_type).order_by(desc(Level.level))
    ).fetchall():
        if lvl.exp <= target_user.exp:
            target_user.level = lvl.level
            break
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/reset", response_model=UserSeasonPassSchema)
def reset(request: RegisterRequestSchema, sess=Depends(session)):
    target_season = get_pass(sess, request.pass_type, request.season_index, include_exp=True)
    if not target_season:
        raise SeasonNotFoundError(f"{request.pass_type}:{request.season_index} not found")
    target_user = sess.scalar(
        select(UserSeasonPass)
        .where(UserSeasonPass.avatar_addr == request.avatar_addr,
               UserSeasonPass.season_pass_id == target_season.id)
    )
    if not target_user:
        raise UserNotFoundError(f"User {request.avatar_addr} not found. Register first.")

    target_user.level = 0
    target_user.exp = 0
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/change-pass-time", response_model=SeasonPassSchema)
def change_pass_time(request: SeasonChangeRequestSchema, sess=Depends(session)):
    target_season = get_pass(sess, request.pass_type, request.season_index, include_exp=True)
    if not target_season:
        raise SeasonNotFoundError(f"{request.pass_type}:{request.season_index} not found")

    target_season.start_timestamp = request.start_timestamp
    target_season.end_timestamp = request.end_timestamp
    sess.add(target_season)
    sess.commit()
    sess.refresh(target_season)
    return target_season
