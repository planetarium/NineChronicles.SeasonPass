from datetime import date
from typing import List

from pydantic import BaseModel as BaseSchema

from common.enums import ActionType


class ItemInfoSchema(BaseSchema):
    id: int
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


class ExpInfoSchema(BaseSchema):
    action_type: ActionType
    exp: int

    class Config:
        from_attributes = True
