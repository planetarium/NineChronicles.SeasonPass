from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import structlog
from app.celery_app import app
from app.config import config
from shared.enums import TxStatus
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

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
    gql = GQLClient(config.converted_gql_url_map, config.headless_jwt_secret)

    try:
        now = datetime.now(tz=timezone.utc)
        claim_list = (
            sess.query(Claim)
            .filter(
                Claim.tx_status.in_([TxStatus.CREATED, TxStatus.INVALID]),
                Claim.created_at <= now - timedelta(minutes=5),
                Claim.reward_list != [],
                Claim.tx.isnot(None),
            )
            .order_by(Claim.nonce.asc())
            .limit(100)
        )
        if not claim_list:
            logger.info("No claim to stage")
            return

        for claim in claim_list:
            success, msg, _ = gql.stage(claim.planet_id, bytes.fromhex(claim.tx))
            if not success:
                message = f"Failed to stage tx with nonce {claim.nonce}: {msg}"
                logger.error(message)
                raise Exception(message)

            claim.tx_status = TxStatus.STAGED
            sess.add(claim)
            sess.commit()
    except Exception as e:
        logger.error("Error processing retry stage", exc_info=e)
        self.retry(exc=e)
    finally:
        sess.close()
