from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel as BaseSchema
from pydantic import model_validator
from shared.enums import ActionType, PassType


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


class SeasonPassSchema(BaseSchema):
    id: int
    pass_type: str
    season_index: int
    start_timestamp: Optional[datetime]
    end_timestamp: Optional[datetime]
    reward_list: List[RewardSchema]
    instant_exp: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class LevelInfoSchema(BaseSchema):
    level: int
    exp: int


class ExpInfoSchema(BaseSchema):
    action_type: ActionType
    exp: int


class SeasonPassDetailSchema(BaseSchema):
    id: int
    pass_type: PassType
    season_index: int
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: List[RewardSchema] = []
    instant_exp: int = 0
    exp_list: List[dict] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CreateExpSchema(BaseSchema):
    action_type: ActionType
    exp: int


class CreateSeasonPassSchema(BaseSchema):
    pass_type: PassType
    season_index: int
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: List[RewardSchema] = []
    instant_exp: int = 0
    exp_list: List[CreateExpSchema] = []


class UpdateSeasonPassSchema(BaseSchema):
    start_timestamp: Optional[datetime] = None
    end_timestamp: Optional[datetime] = None
    reward_list: Optional[List[RewardSchema]] = None
    instant_exp: Optional[int] = None
    exp_list: Optional[List[CreateExpSchema]] = None
