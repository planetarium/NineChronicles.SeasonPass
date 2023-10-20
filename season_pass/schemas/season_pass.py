from datetime import date
from typing import List

from pydantic import BaseModel as BaseSchema


class SeasonPassSchema(BaseSchema):
    id: int
    start_date: date
    end_date: date
    reward_list: List


class LevelInfoSchema(BaseSchema):
    level: int
    exp: int
