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
    odin_gql_url: str = "https://odin-rpc.nine-chronicles.com/graphql"
    heimdall_gql_url: str = "https://heimdall-rpc.nine-chronicles.com/graphql"
    thor_gql_url: str = "https://thor-rpc.nine-chronicles.com/graphql"
    model_config = SettingsConfigDict(env_file=(".env"), env_prefix="API_")


config = Settings()
