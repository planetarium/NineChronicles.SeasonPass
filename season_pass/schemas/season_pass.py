from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel as BaseSchema, model_validator

from common.enums import ActionType, PassType


class ItemInfoSchema(BaseSchema):
    id: int
    amount: int


class CurrencyInfoSchema(BaseSchema):
    ticker: str
    amount: float


class ClaimSchema(BaseSchema):
    ticker: str
    amount: int
    decimal_places: int = 0
    id: Optional[str] = ""

    # Compatibility
    @model_validator(mode="after")
    def _id(self):
        self.id = self.ticker.split("_")[-1]
        return self


class NewRewardSchema(BaseSchema):
    level: int
    normal: List[ClaimSchema]
    premium: List[ClaimSchema]


class RewardDetailSchema(BaseSchema):
    item: List[ItemInfoSchema]
    currency: List[CurrencyInfoSchema]


class RewardSchema(BaseSchema):
    level: int
    normal: RewardDetailSchema
    premium: RewardDetailSchema


class SimpleSeasonPassSchema(BaseSchema):
    id: int
    pass_type: PassType
    season_index: int

    class Config:
        from_attributes = True


class SeasonPassSchema(SimpleSeasonPassSchema):
    start_date: Optional[date]
    end_date: Optional[date]
    start_timestamp: Optional[datetime]
    end_timestamp: Optional[datetime]
    reward_list: List[RewardSchema]
    repeat_last_reward: bool


class NewSeasonPassSchema(BaseSchema):
    id: int
    start_date: date
    end_date: date
    start_timestamp: datetime
    end_timestamp: datetime
    reward_list: List[NewRewardSchema]


class LevelInfoSchema(BaseSchema):
    level: int
    exp: int


class ExpInfoSchema(BaseSchema):
    action_type: ActionType
    exp: int

    class Config:
        from_attributes = True
