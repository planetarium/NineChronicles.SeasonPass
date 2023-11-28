import logging
import os
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

try:
    loglevel = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper())
except AttributeError:
    loglevel = logging.INFO

logger = logging.Logger("season_pass_logger")
logger.setLevel(loglevel)

handler = logging.StreamHandler()
handler.setLevel(loglevel)
logger.addHandler(handler)


@dataclass
class Config:
    stage: str
    account_id: str
    region_name: str

    odin_validator_url: str
    heimdall_validator_url: str

    planet_url: str
    kms_key_id: Optional[str] = None
    jwt_token_secret: Optional[str] = None

    odin_gql_url: Optional[str] = None
    odin_scan_url: Optional[str] = None
    heimdall_gql_url: Optional[str] = None
    heimdall_scan_url: Optional[str] = None
