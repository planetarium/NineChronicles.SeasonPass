from typing import List

from pydantic import BaseModel as BaseSchema, model_validator

from season_pass.schemas.season_pass import ItemInfoSchema, CurrencyInfoSchema


class UserSeasonPassSchema(BaseSchema):
    agent_addr: str = ""
    avatar_addr: str
    season_pass_id: int = 0
    level: int = 0
    exp: int = 0
    is_premium: bool = False
    is_premium_plus: bool = False
    last_normal_claim: int = 0
    last_premium_claim: int = 0

    @model_validator(mode="after")
    def lowercase(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()

    class Config:
        from_attributes = True


class UpgradeRequestSchema(BaseSchema):
    agent_addr: str
    avatar_addr: str
    season_id: int
    is_premium: bool = False
    is_premium_plus: bool = False

    @model_validator(mode="after")
    def lowercase(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()


class ClaimRequestSchema(BaseSchema):
    agent_addr: str
    avatar_addr: str
    season_id: int

    @model_validator(mode="after")
    def lowercase(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()


class ClaimResultSchema(BaseSchema):
    items: List[ItemInfoSchema]
    currencies: List[CurrencyInfoSchema]
    user: UserSeasonPassSchema
