from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import structlog
from shared.enums import TxStatus
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from app.celery_app import app
from app.config import config
from app.tasks.claim_task import configured_planet_ids

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


@app.task(
    name="season_pass.process_retry_stage",
    bind=True,
    max_retries=30,
    default_retry_delay=60,
    acks_late=True,
    retry_backoff=True,
    queue="claim_queue",
)
def process_retry_stage(self, message: Dict[str, Any] = None):
    """
    Retry stage transactions to the blockchain.

    Args:
        self: 태스크 인스턴스 (bind=True로 인해 자동으로 전달됨)
        message: send_to_worker에서 전달되는 메시지 (옵션)
    """
    sess = scoped_session(sessionmaker(bind=engine))
    gql = GQLClient(
        config.converted_gql_url_map,
        config.headless_jwt_secret,
        timeout=config.gql_timeout,
    )

    try:
        now = datetime.now(tz=timezone.utc)
        # 플래닛마다 따로 고르고, 실패하면 그 플래닛의 남은 건만 이번 차례에서 건너뛴다(다음 beat 5분 뒤).
        # 예전엔 모든 플래닛을 nonce 순으로 100건 고르고 첫 실패에서 묶음 전체를 재시도해, 멈춘
        # 플래닛(2026-11-16 오딘 INVALID 51건)이 다른 플래닛의 재스테이징까지 막을 수 있었다.
        # 같은 플래닛은 nonce 순서대로 들어가야 하므로 실패한 건 뒤를 보내 봐야 소용없다.
        failed_planets = []
        for planet_id in configured_planet_ids():
            claim_list = (
                sess.query(Claim)
                .filter(
                    Claim.planet_id == planet_id,
                    Claim.tx_status.in_([TxStatus.CREATED, TxStatus.INVALID]),
                    Claim.created_at <= now - timedelta(minutes=5),
                    Claim.reward_list != [],
                    Claim.tx.isnot(None),
                )
                .order_by(Claim.nonce.asc())
                .limit(100)
                .all()
            )
            for claim in claim_list:
                try:
                    success, msg, _ = gql.stage(planet_id, bytes.fromhex(claim.tx))
                except Exception as e:
                    success, msg = False, repr(e)
                if not success:
                    logger.error(
                        "Failed to stage tx",
                        planet_id=planet_id,
                        nonce=claim.nonce,
                        msg=msg,
                    )
                    failed_planets.append(planet_id)
                    break

                claim.tx_status = TxStatus.STAGED
                sess.add(claim)
                sess.commit()
        if failed_planets:
            logger.error("Retry stage finished with failures", planets=failed_planets)
    except Exception as e:
        logger.error("Error processing retry stage", exc_info=e)
        self.retry(exc=e)
    finally:
        sess.close()
