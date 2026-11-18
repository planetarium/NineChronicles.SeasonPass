"""claim_tx_history 테이블을 쓸 수 있는지 확인한다(재서명 B2).

이미지가 마이그레이션보다 먼저 뜨거나, 마이그레이션을 다른 역할로 돌려 앱 역할에 권한이 없으면
이 테이블을 쓰는 SQL 이 실패한다. 업무 세션 안에서 실패하면 그 트랜잭션 전체가 abort 되므로,
업무 세션과 다른 연결에서 미리 확인하고 안 되면 재서명·기록 확정만 끈다.
"""
import logging
import threading
import time
from typing import Dict, Tuple

from sqlalchemy import text

# shared 는 structlog 에 의존하지 않는다(_graphql.py 와 같이 표준 logging).
logger = logging.getLogger(__name__)

# 권한은 하나씩 따로 묻는다. has_table_privilege(…, 'SELECT, INSERT') 처럼 쉼표로 묶으면
# "하나라도 있으면 true" 라서 INSERT 권한이 없어도 통과한다.
# UPDATE 는 앱이 쓰지 않지만 운영 절차(reason·bound_nonce 갱신)를 앱 역할로 할 수 있는지도 본다.
READY_SQL = text(
    """
SELECT CASE WHEN to_regclass('claim_tx_history') IS NULL THEN false ELSE
         has_table_privilege(to_regclass('claim_tx_history'), 'SELECT')
     AND has_table_privilege(to_regclass('claim_tx_history'), 'INSERT')
     AND has_table_privilege(to_regclass('claim_tx_history'), 'UPDATE')
     AND has_sequence_privilege(pg_get_serial_sequence('claim_tx_history', 'id'), 'USAGE')
END
"""
)

# 준비되지 않았다는 결과는 이 시간만 기억한다(마이그레이션 직후 재시작 없이 켜지게).
NOT_READY_TTL_SEC = 60.0

_lock = threading.Lock()
# 엔진(연결 주소·역할)별 결과. True 는 프로세스 안에 영구히, False 는 NOT_READY_TTL_SEC 동안.
_cache: Dict[str, Tuple[bool, float]] = {}


def _key(engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def history_table_ready(engine) -> bool:
    """claim_tx_history 가 있고, 지금 연결 역할에 SELECT·INSERT·UPDATE·시퀀스 USAGE 가 모두 있으면 True.

    업무 세션과 다른 연결(engine.connect())에서 확인하므로 실패해도 업무 트랜잭션은 abort 되지 않는다.
    예외는 False 로 본다."""
    key = _key(engine)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and (cached[0] or now < cached[1]):
            return cached[0]
    try:
        with engine.connect() as conn:
            # 카탈로그만 보지만, 잠금 경합이 있어도 호출한 작업이 오래 묶이지 않게 짧게 둔다.
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            ready = bool(conn.scalar(READY_SQL))
            conn.rollback()
    except Exception as e:
        logger.warning("claim_tx_history readiness check failed: %r", e)
        ready = False
    with _lock:
        _cache[key] = (ready, now + NOT_READY_TTL_SEC)
    return ready


def reset_history_table_ready_cache() -> None:
    """시험용: 캐시를 비운다."""
    with _lock:
        _cache.clear()
