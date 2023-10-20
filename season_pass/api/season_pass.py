from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select

from common.models.season_pass import SeasonPass, Level
from season_pass.dependencies import session
from season_pass.exceptions import SeasonNotFoundError
from season_pass.schemas.season_pass import LevelInfoSchema, SeasonPassSchema

router = APIRouter(
    prefix="/season-pass",
    tags=["SeasonPass"],
)


@router.get("/current", response_model=SeasonPassSchema)
def current_season(sess=Depends(session)):
    today = datetime.now(tz=timezone.utc).date()
    curr_season = sess.scalar(select(SeasonPass).where(SeasonPass.start_date <= today, SeasonPass.end_date >= today))
    if not curr_season:
        raise SeasonNotFoundError("No active season pass for today.")
    return curr_season


@router.get("/level", response_model=List[LevelInfoSchema])
def level_info(sess=Depends(session)):
    return sess.scalars(select(Level).order_by(Level.level)).fetchall()
