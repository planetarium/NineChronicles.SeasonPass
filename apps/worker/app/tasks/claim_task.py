import time
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


# process_retry_claim 한 번에 새 claim 을 시작하는 시간 상한. beat expires(240초)와 claim-worker
# terminationGracePeriodSeconds(180초)보다 짧게 — 마지막 건(최대 수 초)까지 끝나도 유예 안에 든다.
RETRY_CLAIM_BUDGET_SEC = 150


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
        # planet_id 는 consume_claim_message 가 claim 을 읽은 뒤 실패하면 예외에 실어 보낸다.
        logger.error(
            "Error processing claim message",
            message=message,
            planet_id=getattr(exc, "planet_id", None),
            exc_info=exc,
        )
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
        # 플래닛마다 100건씩 따로 고른다(process_retry_stage 와 같은 방식). 예전엔 모든 플래닛을
        # created_at 순으로 100건 골라, 한 플래닛에 오래된 건이 100건 넘게 쌓이면 다른 플래닛 건은
        # 창에 들지 못해 재처리되지 않았다.
        #
        # 한 건이 실패해도(노드가 내려간 플래닛 등) 다른 플래닛은 계속 처리한다. 예전엔 첫 실패에서
        # 묶음 전체를 재시도해, 맨 앞의 thor 클레임 하나가 2026-11-17 인터널 오딘 복구를 막았다.
        # 실패한 건은 다음 beat(5분)에 다시 고른다. 실패한 플래닛의 남은 건은 이번엔 건너뛴다 —
        # stage 실패가 이어지면 nonce 만 줄줄이 할당돼 구멍 뒤에 갇히고, 실행도 길어진다.
        #
        # 고른 uuid 만 들고 조회 트랜잭션은 바로 끝낸다. 처리는 consume_claim_message 가 자기
        # 세션으로 하므로, 열어 두면 플래닛 수 × 100건을 처리하는 내내 idle-in-transaction 으로
        # 남아 autovacuum 을 막는다.
        # 플래닛을 번갈아 한 건씩 처리하고(라운드로빈), RETRY_CLAIM_BUDGET_SEC 이 지나면 새 claim 을
        # 시작하지 않는다. 플래닛별로 다 고르면 한 번 실행이 플래닛 수 × 100건까지 늘어 beat 의
        # expires(240초)·claim-worker 종료 유예(180초)를 넘고, 뒤 플래닛은 매번 잘린다. 남은 건은
        # 다음 beat 에 다시 고른다.
        pending = {}
        for planet_id in configured_planet_ids():
            rows = (
                sess.query(Claim.uuid)
                .filter(
                    Claim.planet_id == planet_id,
                    Claim.created_at <= now - timedelta(minutes=5),
                    Claim.reward_list != [],
                    Claim.tx.is_(None),
                    Claim.nonce.is_(None),
                )
                .order_by(Claim.created_at.asc())
                .limit(100)
                .all()
            )
            if rows:
                pending[planet_id] = [row.uuid for row in rows]
        sess.commit()

        total = sum(len(uuids) for uuids in pending.values())
        failed = 0
        failed_planets = []
        done = 0
        started = time.monotonic()
        for i in range(max((len(uuids) for uuids in pending.values()), default=0)):
            if time.monotonic() - started > RETRY_CLAIM_BUDGET_SEC:
                logger.warning(
                    "Retry claim stopped at time budget",
                    budget_sec=RETRY_CLAIM_BUDGET_SEC,
                    done=done,
                    total=total,
                )
                break
            for planet_id, uuids in pending.items():
                if i >= len(uuids) or planet_id in failed_planets:
                    continue
                uuid = uuids[i]
                done += 1
                try:
                    consume_claim_message(ClaimMessage(uuid=uuid))
                except Exception as e:
                    failed += 1
                    failed_planets.append(planet_id)
                    logger.error(
                        "Error retrying claim",
                        uuid=uuid,
                        planet_id=planet_id,
                        exc_info=e,
                    )

        if not total:
            logger.info("No claim to retry")
            return
        if failed:
            logger.error(
                "Retry claim finished with failures",
                failed=failed,
                total=total,
                planets=failed_planets,
            )
    except Exception as e:
        logger.error("Error processing retry claim", exc_info=e)
        self.retry(exc=e)
    finally:
        sess.close()
