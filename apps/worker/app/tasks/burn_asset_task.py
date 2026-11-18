import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

import structlog
from app.celery_app import app
from app.config import config
from app.utils.aws import Account
from app.utils.nonce_lock import lock_planet_nonce
from shared.enums import PlanetID
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from shared.utils.transaction import create_burn_asset_unsigned_tx, create_signed_tx
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import scoped_session, sessionmaker


logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def get_decimal_places_for_ticker(ticker: str) -> int:
    """티커별 decimal places를 반환합니다."""
    ticker_decimal_map = {
        "NCG": 2,
        "CRYSTAL": 18,
        "MEAD": 18,
    }
    return ticker_decimal_map.get(ticker.upper(), 0)  # 기본값: 0


# 소각 tx 의 nonce 는 어디에도 기록되지 않는다. stage 요청 뒤 다시 실행하면 새 nonce·새 timestamp 로
# 새 tx 를 서명해 한 번 더 소각한다. 그래서:
# - stage 요청 전 실패(잠금 대기 초과 55P03, 노드 nonce 조회 실패, KMS 오류): 서명한 tx 가 프로세스 밖으로
#   나간 적이 없어 다시 해도 이중 소각이 없다. 일시적 오류를 메우려고 PRE_STAGE_RETRIES 번까지 자동 재시도한다.
# - stage 요청 뒤 실패: 자동 재시도하지 않는다. 에러 로그(tx_id·nonce)를 보고 사람이 체인을 확인한다.
# acks_late=False: 워커 기본은 task_acks_late + task_reject_on_worker_lost(celery_app.py)라 stage 뒤
# 워커가 죽으면 메시지가 다시 와서 두 번 소각한다. 받을 때 ack 하는 대가로, 처리 중 워커가 죽으면 그 소각은
# 사라진다 — "Processing burn asset message" 뒤에 줄이 없으면 사람이 체인을 확인한다.
# 실패를 알리는 수단은 워커 로그뿐이다(백오피스는 큐에 넣는 순간 success, 결과 저장·슬랙 알림 없음).
PRE_STAGE_RETRIES = 3
PRE_STAGE_RETRY_SECONDS = 60


@app.task(
    name="season_pass.process_burn_asset",
    bind=True,
    max_retries=PRE_STAGE_RETRIES,
    acks_late=False,
    priority=0,
    queue="claim_queue",
)
def process_burn_asset(self, message: Dict[str, Any]) -> str:
    """
    Process burn asset messages as a Celery task

    Args:
        self: Task instance
        message: The message data containing burn asset information

    Returns:
        str: Processing result message
    """
    sess = scoped_session(sessionmaker(bind=engine))
    ticker = message.get("ticker")
    amount = message.get("amount")
    memo = message.get("memo", "")
    planet_id = message.get("planet_id", "0x000000000000")  # 기본값: ODIN
    nonce = tx_id = None
    stage_attempted = False
    stage_msg = None

    try:
        logger.info("Processing burn asset message", message=message)
        amount = str(amount)
        account = Account(config.kms_key_id, config.region_name)
        gql = GQLClient(
            config.converted_gql_url_map,
            config.headless_jwt_secret,
            timeout=config.gql_timeout,
        )

        # Get decimal places for ticker
        decimal_places = get_decimal_places_for_ticker(ticker)

        # Create Account instance to get the signer address
        owner_hex = account.address

        # Get nonce (GQL과 Claim 테이블의 nonce 중 큰 값 사용)
        planet_id_enum = PlanetID(planet_id.encode())
        # claim 과 같은 서명자 nonce 를 쓰므로 같은 플래닛 잠금을 잡는다. 소각 tx 는 DB 에 nonce 를
        # 남기지 않아, 다음 nonce 계산이 이 tx 를 보려면 노드 대기열에 들어간 뒤(stage)여야 한다.
        # 그래서 stage 까지 쥐고, finally 의 sess.close()(rollback)에서 풀린다. 기다림은
        # LOCK_TIMEOUT 까지이고, 넘기면 서명 전 실패다(PRE_STAGE_RETRIES 번까지 자동 재시도).
        lock_planet_nonce(sess, planet_id_enum)
        chain_nonce = gql.get_next_nonce(planet_id_enum, account.address)
        if chain_nonce < 0:
            # get_next_nonce 는 GQL 오류에 -1 을 준다. DB 값만으로 서명하면 노드 대기열의 claim 과
            # 같은 nonce 를 쓸 수 있다.
            raise RuntimeError("nextTxNonce unavailable from the node")
        nonce = max(
            chain_nonce,
            (
                sess.scalar(
                    select(Claim.nonce)
                    .where(
                        Claim.nonce.is_not(None),
                        Claim.planet_id == planet_id_enum,
                    )
                    .order_by(desc(Claim.nonce))
                    .limit(1)
                )
                or -1
            )
            + 1,
        )

        # Create unsigned transaction
        unsigned_tx = create_burn_asset_unsigned_tx(
            planet_id=planet_id_enum,
            public_key=account.pubkey.hex(),
            address=account.address,
            nonce=nonce,
            owner=owner_hex,
            ticker=ticker,
            decimal_places=decimal_places,
            amount=amount,
            memo=memo,
            timestamp=datetime.now(tz=timezone.utc),
        )

        # AWS KMS로 서명 생성
        signature = account.sign_tx(unsigned_tx)

        # Signed transaction 생성
        signed_tx = create_signed_tx(unsigned_tx, signature)

        # Transaction ID 생성
        tx_id = hashlib.sha256(signed_tx).hexdigest()

        # tx_hex 는 같은 tx 를 다시 stage 할 때 쓴다(tx_id 가 같아 두 번 소각되지 않는다). 단 서명 시각에서
        # tx 수명(메인넷 10분)이 지나면 노드는 stage 에 성공을 돌려주고 버린다 — 그때는 이 nonce 가 구멍이 된다.
        logger.info(
            "Burn asset transaction created successfully",
            planet_id=planet_id,
            owner=owner_hex,
            ticker=ticker,
            amount=amount,
            memo=memo,
            nonce=nonce,
            tx_id=tx_id,
            tx_hex=signed_tx.hex(),
        )

        # Stage transaction
        stage_attempted = True
        success, stage_msg, _ = gql.stage(planet_id_enum, signed_tx)
        if not success:
            raise Exception(
                f"Failed to stage burn asset tx with nonce {nonce}: {stage_msg}"
            )

        logger.info(
            "Burn asset transaction staged successfully",
            planet_id=planet_id,
            nonce=nonce,
            tx_id=tx_id,
        )

        return "Burn asset transaction processed and staged successfully"

    except Exception as exc:
        fields = dict(
            planet_id=planet_id,
            ticker=ticker,
            amount=amount,
            memo=memo,
            nonce=nonce,
            tx_id=tx_id,
            stage_attempted=stage_attempted,
            retries=self.request.retries,
        )
        if stage_attempted:
            logger.error(
                "Burn asset failed after stage request; MAY HAVE BEEN BURNED, "
                "check tx_id on chain before requesting again",
                msg=stage_msg,
                exc_info=exc,
                **fields,
            )
            raise
        if self.request.retries < PRE_STAGE_RETRIES:
            # 서명한 tx 가 나간 적 없음 — 다시 해도 이중 소각이 없다. 잠금은 finally 의 close 로 풀린다.
            logger.warning(
                "Burn asset failed before stage; not burned, retrying",
                countdown=PRE_STAGE_RETRY_SECONDS,
                exc_info=exc,
                **fields,
            )
            raise self.retry(exc=exc, countdown=PRE_STAGE_RETRY_SECONDS)
        logger.error(
            "Burn asset failed before stage; not burned, safe to request again",
            exc_info=exc,
            **fields,
        )
        raise
    finally:
        sess.close()
