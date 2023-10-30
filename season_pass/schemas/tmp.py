from typing import Optional

from pydantic import BaseModel as BaseSchema


class RegisterRequestSchema(BaseSchema):
    agent_addr: str
    avatar_addr: str


class PremiumRequestSchema(BaseSchema):
    avatar_addr: str
    is_premium: bool
    is_premium_plus: bool


class LevelRequestSchema(BaseSchema):
    avatar_addr: str
    level: Optional[int] = None
    exp: Optional[int] = None
