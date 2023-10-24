from datetime import date
from typing import List

from pydantic import BaseModel as BaseSchema


class ItemInfoSchema(BaseSchema):
    item_id: int
    amount: int


class CurrencyInfoSchema(BaseSchema):
    ticker: str
    amount: float


class RewardDetailSchema(BaseSchema):
    item: List[ItemInfoSchema]
    currency: List[CurrencyInfoSchema]


class RewardSchema(BaseSchema):
    level: int
    normal: RewardDetailSchema
    premium: RewardDetailSchema


class SeasonPassSchema(BaseSchema):
    id: int
    start_date: date
    end_date: date
    reward_list: List[RewardSchema]


class LevelInfoSchema(BaseSchema):
    level: int
    exp: int
