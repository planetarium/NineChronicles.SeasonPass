from typing import Optional

from pydantic import BaseModel as BaseSchema, model_validator


class RegisterRequestSchema(BaseSchema):
    agent_addr: str
    avatar_addr: str

    @model_validator(mode="after")
    def lowercase(self):
        self.agent_addr = self.agent_addr.lower()
        self.avatar_addr = self.avatar_addr.lower()


class PremiumRequestSchema(BaseSchema):
    avatar_addr: str
    is_premium: bool
    is_premium_plus: bool

    @model_validator(mode="after")
    def lowercase(self):
        self.avatar_addr = self.avatar_addr.lower()


class LevelRequestSchema(BaseSchema):
    avatar_addr: str
    level: Optional[int] = None
    exp: Optional[int] = None

    @model_validator(mode="after")
    def lowercase(self):
        self.avatar_addr = self.avatar_addr.lower()
