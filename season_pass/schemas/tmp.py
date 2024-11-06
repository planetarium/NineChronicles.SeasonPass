from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel as BaseSchema, model_validator

from common.enums import PlanetID, PassType


class RegisterRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    agent_addr: str
    avatar_addr: str
    pass_type: PassType
    season_index: int

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class PremiumRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    avatar_addr: str
    pass_type: PassType
    season_index: int
    is_premium: bool
    is_premium_plus: bool

    @model_validator(mode="after")
    def sanitize(self):
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class ExpRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    avatar_addr: str
    pass_type: PassType
    season_index: int
    exp: int = 0

    @model_validator(mode="after")
    def sanitize(self):
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class SeasonChangeRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    pass_type: PassType
    season_index: int
    start_timestamp: Optional[str | datetime] = None
    end_timestamp: Optional[str | datetime] = None

    @model_validator(mode="after")
    def sanitize(self):
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        if isinstance(self.start_timestamp, str):
            self.start_timestamp = datetime.strptime(self.timestamp, "%Y-%m-%d %H:%M:%S").astimezone(tz=timezone.utc)
        if isinstance(self.end_timestamp, str):
            self.end_timestamp = datetime.strptime(self.timestamp, "%Y-%m-%d %H:%M:%S").astimezone(tz=timezone.utc)
        return self
