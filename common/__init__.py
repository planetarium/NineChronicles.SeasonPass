from typing import Optional

from pydantic.dataclasses import dataclass

COMMON_LAMBDA_EXCLUDE = [
    "!common",
    "!common/**",
    "common/__pycache__",
    "common/alembic",
    "common/alembic.ini",
    "common/alembic.ini.example",
]


@dataclass
class Config:
    stage: str
    account_id: str
    region_name: str

    planet_url: str
    kms_key_id: Optional[str] = None
    jwt_token_secret: Optional[str] = None
