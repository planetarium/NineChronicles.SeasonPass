import base64
from typing import Optional

from pydantic import AmqpDsn, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.enums import PlanetID


class Settings(BaseSettings):
    pg_dsn: PostgresDsn = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    amqp_dsn: AmqpDsn = "amqp://local_test:password@127.0.0.1:5672/"
    headless_jwt_secret: Optional[str] = None
    db_echo: bool = False
    stage: str = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1
    timeout_keep_alive: int = 5
    gql_url_map: dict[str, str] = {
        "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
        "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
    }

    @property
    def converted_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.gql_url_map.items()}

    model_config = SettingsConfigDict(env_file=(".env"), env_prefix="API_")


config = Settings()
