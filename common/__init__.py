import logging
import os
from typing import Optional

from pydantic.dataclasses import dataclass

SEASONPASS_ADDRESS = "0x0E19A992ad976B4986098813DfCd24B0775AC0AA"

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

    odin_gql_url: str
    heimdall_gql_url: str
    thor_gql_url: str

    kms_key_id: str
    jwt_token_secret: str

    # JWT Headless
    headless_gql_jwt_secret: Optional[str] = None
