from typing import Optional

from pydantic import BaseModel as BaseSchema, model_validator

from common.enums import PlanetID


class RegisterRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    agent_addr: str
    avatar_addr: str

    @model_validator(mode="after")
    def set_planet_id(self):
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))


class PremiumRequestSchema(BaseSchema):
    avatar_addr: str
    is_premium: bool
    is_premium_plus: bool


class LevelRequestSchema(BaseSchema):
    avatar_addr: str
    level: Optional[int] = None
    exp: Optional[int] = None
