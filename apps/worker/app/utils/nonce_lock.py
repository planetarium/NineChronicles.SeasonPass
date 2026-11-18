import hashlib

from sqlalchemy import text

# 다른 용도의 advisory lock 과 키가 겹치지 않게 키에 이름을 섞는다.
_NAMESPACE = b"season-pass:signer-nonce:"


def planet_nonce_lock_key(planet_id: bytes) -> int:
    """플래닛 id 에서 pg_advisory_xact_lock 의 bigint 키를 만든다.

    파이썬 hash() 는 프로세스마다 달라지므로 쓰지 않는다. 파드·프로세스가 달라도 같은
    플래닛이면 같은 키가 나와야 서로를 막는다."""
    digest = hashlib.sha256(_NAMESPACE + bytes(planet_id)).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_planet_nonce(sess, planet_id: bytes) -> None:
    """같은 플래닛의 서명자 nonce 를 계산하는 구간을 한 번에 하나만 지나게 한다.

    서명 키는 하나이고 nonce 는 플래닛별이다. claim 과 burn_asset 이 같은 서명자로 nonce 를
    쓰므로 둘 다 이 잠금을 잡는다. 트랜잭션 잠금이라 commit·rollback(세션 close 포함)에서
    저절로 풀린다 — 풀어 주는 코드를 따로 두지 않는다."""
    sess.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": planet_nonce_lock_key(planet_id)},
    )
