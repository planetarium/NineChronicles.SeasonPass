import base64
from typing import Optional

from pydantic import AmqpDsn, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.enums import PlanetID


class Settings(BaseSettings):
    pg_dsn: PostgresDsn = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    amqp_dsn: AmqpDsn = "amqp://local_test:password@127.0.0.1:5672/"
    gql_url: str = "https://odin-rpc.nine-chronicles.com/graphql"
    headless_jwt_secret: Optional[str] = None
    planet_str: str = "0x000000000000"
    start_block_index: int
    arena_service_jwt_public_key: str

    @property
    def planet_id(self) -> PlanetID:
        return PlanetID(self.planet_str.encode())

    @property
    def arena_service_jwt_public_key_pem(self) -> str:
        return base64.b64decode(self.arena_service_jwt_public_key).decode("utf-8")

    model_config = SettingsConfigDict(env_file=(".env"), env_prefix="TRACKER_")


config = Settings()
