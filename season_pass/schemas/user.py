from typing import Dict

from pydantic import BaseModel as BaseSchema


class UserSeasonPassSchema(BaseSchema):
    agent_addr: str = ""
    avatar_addr: str
    season_pass_id: int = 0
    level: int = 0
    exp: int = 0
    is_premium: bool
    last_normal_claim: int = 0
    last_premium_claim: int = 0

    class Config:
        from_attributes = True


class ClaimRequestSchema(BaseSchema):
    agent_addr: str
    avatar_addr: str
    season_id: int


class ClaimResultSchema(BaseSchema):
    items: Dict[int, int]
    currencies: Dict[str, float]
    user: UserSeasonPassSchema
