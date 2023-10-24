from pydantic.dataclasses import dataclass

COMMON_LAMBDA_EXCLUDE = [
    "!common",
    "!common/**",
    "common/__pycache__",
    "common/layer",
    "common/alembic",
    "common/alembic.ini",
    "common/alembic.ini.example",
    "common/poetry.lock",
    "common/pyproject.toml",
]


@dataclass
class Config:
    stage: str
    account_id: str
    region_name: str
    iap_queue_arn: str
