from fastapi import APIRouter, Depends
from sqlalchemy import select, desc

from dependencies import session
from exceptions import UserNotFoundError
from models.season_pass import Level
from models.user import UserSeasonPass
from schemas.tmp import PremiumRequestSchema, LevelRequestSchema, RegisterRequestSchema
from schemas.user import UserSeasonPassSchema
from utils.season_pass import get_current_season

router = APIRouter(
    prefix="/tmp",
    tags=["Temp"],
)


@router.post("/register", response_model=UserSeasonPassSchema)
def register_user(request: RegisterRequestSchema, sess=Depends(session)):
    current_season = get_current_season(sess)
    new_data = UserSeasonPass(
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
    sess.add(target_user)
    sess.commit()
    sess.refresh(target_user)
    return target_user


@router.post("/level")
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

    target_user.exp = target_level.exp
    target_user.level = target_level.level
