import base64
from typing import Optional

from pydantic import AmqpDsn, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.enums import PlanetID


class Settings(BaseSettings):
    pg_dsn: PostgresDsn = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    celery_broker_url: str = "pyamqp://local_test:password@127.0.0.1:5672/"
    celery_result_backend: str = "redis://127.0.0.1:6379/0"
    headless_jwt_secret: Optional[str] = None
    arena_service_jwt_public_key: str
    gql_url_map: dict[str, str] = {
        "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
        "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
    }
    start_block_index_map: dict[str, int] = {
        "0x000000000000": 13796504,
        "0x000000000001": 5320341,
    }

    @property
    def converted_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.gql_url_map.items()}

    @property
    def arena_service_jwt_public_key_pem(self) -> str:
        return base64.b64decode(self.arena_service_jwt_public_key).decode("utf-8")

    model_config = SettingsConfigDict(env_file=(".env"), env_prefix="TRACKER_")


config = Settings()
