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
