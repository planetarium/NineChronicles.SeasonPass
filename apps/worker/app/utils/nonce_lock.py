import hashlib

from sqlalchemy import text

# 다른 용도의 advisory lock 과 키가 겹치지 않게 키에 이름을 섞는다.
_NAMESPACE = b"season-pass:signer-nonce:"

# 잠금을 기다리는 최대 시간. 죽은 파드(노드 장애 등)의 DB 세션이 트랜잭션 도중이면 Postgres 가
# 그 연결을 끊을 때까지(TCP keepalive, 길면 수 시간) claim 행 잠금·플래닛 잠금이 남는다. 상한이
# 없으면 새 워커가 거기서 끝없이 기다리고, 동시 처리 1이라 다른 플래닛 지급까지 모두 멈춘다.
# 넘기면 lock_not_available(55P03) OperationalError 가 나고, 각 작업의 기존 재시도 경로로 간다
# (process_claim·burn 은 self.retry 60초 뒤, retry_claim 은 그 플래닛만 이번 차례에 건너뜀).
# 평소 대기는 앞 처리의 nonce GQL·KMS 서명(각 제한 5초) 정도라 30초면 넉넉하다.
LOCK_TIMEOUT = "30s"


def planet_nonce_lock_key(planet_id: bytes) -> int:
    """플래닛 id 에서 pg_advisory_xact_lock 의 bigint 키를 만든다.

    파이썬 hash() 는 프로세스마다 달라지므로 쓰지 않는다. 파드·프로세스가 달라도 같은
    플래닛이면 같은 키가 나와야 서로를 막는다."""
    digest = hashlib.sha256(_NAMESPACE + bytes(planet_id)).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def set_lock_timeout(sess) -> None:
    """지금 트랜잭션의 잠금 대기에 LOCK_TIMEOUT 상한을 건다.

    SET LOCAL 이라 이 트랜잭션(commit·rollback 전까지)에만 걸린다. 잠금을 잡는 문장보다 먼저,
    같은 트랜잭션 안에서 불러야 한다."""
    sess.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))


def lock_planet_nonce(sess, planet_id: bytes) -> None:
    """같은 플래닛의 서명자 nonce 를 계산하는 구간을 한 번에 하나만 지나게 한다.

    서명 키는 하나이고 nonce 는 플래닛별이다. claim 과 burn_asset 이 같은 서명자로 nonce 를
    쓰므로 둘 다 이 잠금을 잡는다. 트랜잭션 잠금이라 commit·rollback(세션 close 포함)에서
    저절로 풀린다 — 풀어 주는 코드를 따로 두지 않는다. 기다림은 LOCK_TIMEOUT 까지다."""
    set_lock_timeout(sess)
    sess.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": planet_nonce_lock_key(planet_id)},
    )
