import base64
from typing import Optional

from pydantic import AmqpDsn, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    pg_dsn: PostgresDsn = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    amqp_dsn: AmqpDsn = "amqp://local_test:password@127.0.0.1:5672/"
    gql_url_map: dict[str, str] = {
        "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
        "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
    }
    region_name: str = "us-east-2"
    stage: str = "development"
    headless_jwt_secret: Optional[str] = None

    model_config = SettingsConfigDict(env_file=(".env"), env_prefix="CONSUMER_")


config = Settings()
