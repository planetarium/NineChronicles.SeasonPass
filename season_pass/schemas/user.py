from typing import List

from pydantic import BaseModel as BaseSchema, model_validator

from common.enums import PlanetID
from season_pass.schemas.season_pass import ItemInfoSchema, CurrencyInfoSchema
from season_pass.settings import stage


class UserSeasonPassSchema(BaseSchema):
    planet_id: PlanetID
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
        return self

    class Config:
        from_attributes = True


class RewardItemSchema(BaseSchema):
    id: int
    amount: int


class RewardCurrencySchema(BaseSchema):
    ticker: str
    amount: str


class ClaimSchema(BaseSchema):
    id: str
    amount: int


class UpgradeRewardSchema(BaseSchema):
    items: List[RewardItemSchema] = []
    currencies: List[RewardCurrencySchema] = []
    claims: List[ClaimSchema] = []


class UpgradeRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    agent_addr: str
    avatar_addr: str
    season_id: int
    is_premium: bool = False
    is_premium_plus: bool = False
    g_sku: str
    a_sku: str
    reward_list: UpgradeRewardSchema = None

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ClaimRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    agent_addr: str
    avatar_addr: str
    season_id: int

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ClaimResultSchema(BaseSchema):
    items: List[ItemInfoSchema]
    currencies: List[CurrencyInfoSchema]
    user: UserSeasonPassSchema
