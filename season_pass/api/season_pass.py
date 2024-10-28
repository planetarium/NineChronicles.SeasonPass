from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select

from common.enums import PassType
from common.models.season_pass import Level, Exp
from common.utils.season_pass import get_current_season
from season_pass.dependencies import session
from season_pass.exceptions import SeasonNotFoundError
from season_pass.schemas.season_pass import LevelInfoSchema, SeasonPassSchema, ExpInfoSchema

router = APIRouter(
    prefix="/season-pass",
    tags=["SeasonPass"],
)


@router.get("/current", response_model=SeasonPassSchema)
def current_season(pass_type: PassType, sess=Depends(session)):
    curr_season = get_current_season(sess, pass_type)
    if not curr_season:
        raise SeasonNotFoundError("No active season pass for today")

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
            for reward in curr_season.reward_list
        ]
    )


@router.get("/level", response_model=List[LevelInfoSchema])
def level_info(pass_type: PassType, sess=Depends(session)):
    return sess.scalars(select(Level).where(Level.pass_type == pass_type).order_by(Level.level)).fetchall()


@router.get("/exp", response_model=List[ExpInfoSchema])
def exp_info(pass_type: PassType, sess=Depends(session)):
    return sess.scalars(select(Exp).where(Exp.pass_type == pass_type)).fetchall()
