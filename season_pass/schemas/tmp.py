from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel as BaseSchema, model_validator

from common.enums import PlanetID


class RegisterRequestSchema(BaseSchema):
    planet_id: PlanetID | str = PlanetID.ODIN
    agent_addr: str
    avatar_addr: str

    @model_validator(mode="after")
    def sanitize(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()
        if isinstance(self.planet_id, str):
            self.planet_id = PlanetID(bytes(self.planet_id, "utf-8"))
        return self


class PremiumRequestSchema(BaseSchema):
    avatar_addr: str
    is_premium: bool
    is_premium_plus: bool

    @model_validator(mode="after")
    def lowercase(self):
        self.avatar_addr = self.avatar_addr.lower()
        return self


class LevelRequestSchema(BaseSchema):
    avatar_addr: str
    level: Optional[int] = None
    exp: Optional[int] = None

    @model_validator(mode="after")
    def lowercase(self):
        self.avatar_addr = self.avatar_addr.lower()
        return self


class SeasonChangeRequestSchema(BaseSchema):
    season_id: int
    timestamp: str | datetime

    @model_validator(mode="after")
    def to_datetime(self):
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.strptime(self.timestamp, "%Y-%m-%d %H:%M:%S").astimezone(tz=timezone.utc)
        return self
