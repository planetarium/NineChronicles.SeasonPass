from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel as BaseSchema
from pydantic import Field, model_validator

from common.enums import PassType, PlanetID
from season_pass.schemas.season_pass import ClaimSchema, SimpleSeasonPassSchema
from season_pass.settings import stage


class UserSeasonPassSchema(BaseSchema):
    planet_id: PlanetID
    agent_addr: str = ""
    avatar_addr: str
    season_pass: SimpleSeasonPassSchema
    level: int = 0
    exp: int = 0
    is_premium: bool = False
    is_premium_plus: bool = False
    last_normal_claim: int = 0
    last_premium_claim: int = 0
    claim_limit_timestamp: Optional[datetime] = None

    @model_validator(mode="after")
    def lowercase(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        return self

    class Config:
        from_attributes = True


class UpgradeRequestSchema(BaseSchema):
    planet_id: PlanetID | str = (
        PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    )
    agent_addr: str
    avatar_addr: str
    pass_type: str | PassType
    season_index: int
    is_premium: bool = False
    is_premium_plus: bool = False
    g_sku: str
    a_sku: str
    reward_list: List[ClaimSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()

        if not self.agent_addr.startswith("0x"):
            self.agent_addr = f"0x{self.agent_addr}"
        if not self.avatar_addr.startswith("0x"):
            self.avatar_addr = f"0x{self.avatar_addr}"

        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))

        if isinstance(self.pass_type, str):
            self.pass_type = PassType(self.pass_type)

        return self


class ClaimRequestSchema(BaseSchema):
    planet_id: PlanetID | str = (
        PlanetID.ODIN if stage == "mainnet" else PlanetID.ODIN_INTERNAL
    )
    agent_addr: str
    avatar_addr: str
    pass_type: PassType
    season_index: int
    force: bool = False
    prev: bool = False

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ClaimResultSchema(BaseSchema):
    user: UserSeasonPassSchema
    reward_list: List[ClaimSchema] = []
