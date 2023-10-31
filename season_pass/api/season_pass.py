from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select

from common.models.season_pass import Level
from common.utils.season_pass import get_current_season
from season_pass.dependencies import session
from season_pass.exceptions import SeasonNotFoundError
from season_pass.schemas.season_pass import LevelInfoSchema, SeasonPassSchema, ExpInfoSchema

router = APIRouter(
    prefix="/season-pass",
    tags=["SeasonPass"],
)


@router.get("/current", response_model=SeasonPassSchema)
def current_season(sess=Depends(session)):
    curr_season = get_current_season(sess)
    if not curr_season:
        raise SeasonNotFoundError("No active season pass for today.")
    return curr_season


@router.get("/level", response_model=List[LevelInfoSchema])
def level_info(sess=Depends(session)):
    return sess.scalars(select(Level).order_by(Level.level)).fetchall()


@router.get("/exp", response_model=List[ExpInfoSchema])
def exp_info(sess=Depends(session)):
    curr_season = get_current_season(sess, include_exp=True)
    return curr_season.exp_list
