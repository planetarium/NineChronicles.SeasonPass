from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.enums import PlanetID


class Settings(BaseSettings):
    pg_dsn: str = "postgresql://local_test:password@127.0.0.1:5432/season_pass"
    broker_url: str = "pyamqp://local_test:password@127.0.0.1:5672/"
    result_backend: str = "redis://127.0.0.1:6379/0"
    gql_timeout: float = 5.0
    gql_url_map: dict[str, str] = {
        "0x000000000000": "https://odin-rpc.nine-chronicles.com/graphql",
        "0x000000000001": "https://heimdall-rpc.nine-chronicles.com/graphql",
        "0x000000000003": "https://thor-rpc.nine-chronicles.com/graphql",
    }
    region_name: str = "us-east-2"
    kms_key_id: str
    stage: str = "development"
    headless_jwt_secret: Optional[str] = None
    # 소각(process_burn_asset) 최종 실패를 알릴 Slack incoming webhook 주소(비밀값 — 시크릿으로 넣는다).
    # 없으면 에러 로그만 남는다.
    burn_alert_webhook_url: Optional[str] = None

    # --- 재서명(B2): 만료된 claim tx 를 같은 nonce 로 다시 서명해 nonce 구멍을 메운다 ---
    # 기본값은 꺼짐 — 이미지만 올려서는 동작이 바뀌지 않는다. 추적기 TRACKER_RESIGN_MODE 와 같은 값을 쓴다
    # (켤 때 워커 → 추적기, 끌 때 추적기 → 워커).
    resign_mode: Literal["off", "dry_run", "on"] = "off"
    # 이보다 오래된 tx(서명 시각 t 기준)만 재서명 후보. 메인넷은 노드 tx 수명(10분) + 실측 시계차 + 여유.
    resign_after_minutes: float = 15.0
    # nonce 조회 노드의 tip 이 이만큼 그대로면 체인 정지로 보고 재서명하지 않는다.
    chain_stall_minutes: float = 4.0
    # 기준 노드 tip − nonce 조회 노드 tip 이 이보다 크면 그 nonce 를 믿지 않는다. 직전 관측 뒤 nonce 조회
    # 노드가 기준 노드보다 이만큼 넘게 더 올라왔어도(따라잡는 중) 보류한다.
    validator_lag_blocks: int = 5
    # 직전 관측 뒤 nonce 조회 노드 tip 의 분당 증가가 이보다 크면 따라잡는 중으로 보고 보류한다.
    # 오딘 7.65초/블록 ≈ 분당 7.8블록의 약 2배. beat 하나가 빠져 간격이 10분이 돼도 속도는 같다.
    validator_max_blocks_per_minute: float = 16.0
    # 한 claim 이 구멍(nonce == 검증자 nextTxNonce)인 채 아래 시간 창 안에 다시 서명되는 최대 횟수.
    max_resigns: int = 3
    # 위 상한을 세는 시간 창. 창이 지나면 다시 시도한다.
    resign_cap_window_hours: float = 6.0
    # 한 회차에 한 플래닛에서 재서명할 최대 건수(시간 상한과 따로 건수도 묶는다).
    resign_batch_limit: int = 20
    # 지금 코드로 다시 만든 tx 가 이보다 크면 재서명하지 않는다(블록 tx 한도 1MB, claim_memo.py).
    resign_max_tx_bytes: int = 1_000_000
    # 플래닛별 nonce·tip 조회 주소. 없으면 gql_url_map(stage 노드). 하임달 메인넷은 stage 가 rpc 라
    # 검증자 주소를 넣는다 — rpc 대기열에는 검증자가 못 받은 tx 가 있을 수 있어 구멍이 안 보인다.
    nonce_gql_url_map: dict[str, str] = {}
    # 플래닛별 기준 노드 주소(nonce 조회 노드와 다른 노드). 없으면 stage 노드가 nonce 조회 노드와 다를 때
    # stage 노드를 쓰고, 그것도 같으면 그 플래닛은 재서명하지 않는다(만료 건도 지금처럼 restage).
    reference_gql_url_map: dict[str, str] = {}

    @property
    def converted_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.gql_url_map.items()}

    @property
    def converted_nonce_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.nonce_gql_url_map.items()}

    @property
    def converted_reference_gql_url_map(self) -> dict[PlanetID, str]:
        return {PlanetID(k.encode()): v for k, v in self.reference_gql_url_map.items()}

    model_config = SettingsConfigDict(
        env_file=(".env", "test.env"), env_prefix="WORKER_"
    )


config = Settings()
