import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

import structlog
from shared.enums import PlanetID
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from shared.utils.transaction import create_burn_asset_unsigned_tx, create_signed_tx
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import scoped_session, sessionmaker

from app.celery_app import app
from app.config import config
from app.utils.aws import Account
from app.utils.nonce_lock import lock_planet_nonce

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


# At most once. The burn tx's nonce is recorded nowhere, so a second run after stage signs a
# NEW tx (new nonce, new timestamp) and burns again. Hence no self.retry, and acks_late=False:
# the worker default is task_acks_late + task_reject_on_worker_lost (celery_app.py), which
# would redeliver the message if the worker died after stage. The cost: a worker killed
# mid-task drops the burn — "Processing burn asset message" with no line after it means a
# person checks the chain. Every failure is logged once, saying whether stage was attempted.
@app.task(
    name="season_pass.process_burn_asset",
    bind=True,
    max_retries=0,
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
        # LOCK_TIMEOUT 까지이고, 넘기면 서명 전 실패로 끝난다(자동 재시도 없음).
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

        # tx_hex 로 사람이 같은 tx 를 다시 stage 할 수 있다(tx_id 가 같아 두 번 소각되지 않는다).
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
        if stage_attempted:
            event = (
                "Burn asset failed after stage request; MAY HAVE BEEN BURNED, "
                "check tx_id on chain before requesting again"
            )
        else:
            event = "Burn asset failed before stage; not burned, safe to request again"
        logger.error(
            event,
            planet_id=planet_id,
            ticker=ticker,
            amount=amount,
            memo=memo,
            nonce=nonce,
            tx_id=tx_id,
            stage_attempted=stage_attempted,
            msg=stage_msg,
            exc_info=exc,
        )
        raise
    finally:
        sess.close()
