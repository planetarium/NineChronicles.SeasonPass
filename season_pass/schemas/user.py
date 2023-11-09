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

    class Config:
        from_attributes = True


class UpgradeRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    agent_addr: str
    avatar_addr: str
    season_id: int
    is_premium: bool = False
    is_premium_plus: bool = False

    @model_validator(mode="after")
    def set_planet_id(self):
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ClaimRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    agent_addr: str
    avatar_addr: str
    season_id: int

    @model_validator(mode="after")
    def set_planet_id(self):
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ClaimResultSchema(BaseSchema):
    items: List[ItemInfoSchema]
    currencies: List[CurrencyInfoSchema]
    user: UserSeasonPassSchema
