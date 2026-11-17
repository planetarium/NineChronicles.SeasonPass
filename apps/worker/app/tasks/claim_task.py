from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

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


def configured_planet_ids() -> List[bytes]:
    """`gql_url_map` 에 접속 주소가 있는 플래닛. claim.planet_id 와 같은 bytes 형태."""
    return [planet_id.encode() for planet_id in config.gql_url_map]


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
            sess.query(Claim.uuid, Claim.planet_id)
            .filter(
                Claim.created_at <= now - timedelta(minutes=5),
                Claim.reward_list != [],
                Claim.tx.is_(None),
                Claim.nonce.is_(None),
                Claim.planet_id.in_(configured_planet_ids()),
            )
            .order_by(Claim.created_at.asc())
            .limit(100)
            .all()
        )

        if not claim_ids:
            logger.info("No claim to retry")
            return

        # 한 건이 실패해도(노드가 내려간 플래닛 등) 뒤의 클레임은 계속 처리한다. 예전엔 첫 실패에서
        # 묶음 전체를 재시도해, 맨 앞의 thor 클레임 하나가 2026-11-17 인터널 오딘 복구를 막았다.
        # 실패한 건은 다음 beat(5분)에 다시 고른다. 실패한 플래닛의 남은 건은 이번엔 건너뛴다 —
        # stage 실패가 이어지면 nonce 만 줄줄이 할당돼 구멍 뒤에 갇히고, 실행도 길어진다.
        failed = 0
        failed_planets = set()
        for uuid, planet_id in claim_ids:
            if planet_id in failed_planets:
                continue
            try:
                consume_claim_message(ClaimMessage(uuid=uuid))
            except Exception as e:
                failed += 1
                failed_planets.add(planet_id)
                logger.error("Error retrying claim", uuid=uuid, exc_info=e)
        if failed:
            logger.error(
                "Retry claim finished with failures",
                failed=failed,
                total=len(claim_ids),
            )
    except Exception as e:
        logger.error("Error processing retry claim", exc_info=e)
        self.retry(exc=e)
    finally:
        sess.close()
