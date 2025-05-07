from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import structlog
from app.celery_app import app
from app.config import config
from app.consumers.claim_consumer import consume_claim_message
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


@app.task(
    name="season_pass.process_claim",
    bind=True,
    max_retries=30,
    default_retry_delay=60,
    acks_late=True,
    priority=0,
    queue="claim_queue",
)
def process_claim(self, message: Dict[str, Any]) -> str:
    """
    Process claim messages as a Celery task

    Args:
        self: Task instance
        message: The message data from RabbitMQ/task queue

    Returns:
        str: Processing result message
    """
    try:
        logger.info("Processing claim message", message=message)
        claim_message = ClaimMessage.model_validate(message)
        consume_claim_message(claim_message)
        return "Claim message processed successfully"
    except Exception as exc:
        logger.error("Error processing claim message", message=message, exc_info=exc)
        self.retry(exc=exc)


@app.task(
    name="season_pass.process_retry_claim",
    bind=True,
    max_retries=30,
    default_retry_delay=60,
    acks_late=True,
    retry_backoff=True,
    queue="claim_queue",
)
def process_retry_claim(self, message: Dict[str, Any] = None):
    """
    Retry process_claim task

    Args:
        self: 태스크 인스턴스 (bind=True로 인해 자동으로 전달됨)
        message: send_to_worker에서 전달되는 메시지 (옵션)
    """
    sess = scoped_session(sessionmaker(bind=engine))

    try:
        now = datetime.now(tz=timezone.utc)
        claim_ids = (
            sess.query(Claim.uuid)
            .filter(
                Claim.created_at <= now - timedelta(minutes=5),
                Claim.reward_list != [],
                Claim.tx.is_(None),
                Claim.nonce.is_(None),
            )
            .order_by(Claim.created_at.asc())
            .limit(100)
        )
        if not claim_ids:
            logger.info("No claim to retry")
            return

        for claim_id in claim_ids:
            claim_message = ClaimMessage(uuid=claim_id)
            consume_claim_message(claim_message)
    except Exception as e:
        logger.error("Error processing retry claim", exc_info=e)
        self.retry(exc=e)
    finally:
        sess.close()
