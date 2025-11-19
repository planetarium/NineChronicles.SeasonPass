from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.enums import PlanetID


class Settings(BaseSettings):
    pg_dsn: str = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    celery_broker_url: str = "pyamqp://local_test:password@127.0.0.1:5672/"
    celery_result_backend: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = "default_secret_key"
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
        "0x000000000003": "https://thor-rpc.nine-chronicles.com/graphql",
    }

    @property
    def converted_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.gql_url_map.items()}

    model_config = SettingsConfigDict(
        env_file=(".env"), env_prefix="API_", validate_default=True
    )


config = Settings()
