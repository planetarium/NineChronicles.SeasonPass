"""워커 동시 처리 수와 beat 일정 만료."""
from pathlib import Path

from app.celery_app import app
from app.tasks.claim_task import process_retry_claim
from app.utils.nonce_lock import planet_nonce_lock_key
from celery.app.task import Context

DOCKERFILE = Path(__file__).resolve().parents[3] / "Dockerfile.Worker"


def test_worker_concurrency_is_one():
    assert app.conf.worker_concurrency == 1
    cmd = [
        line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD")
    ]
    assert len(cmd) == 1 and "--concurrency=1" in cmd[0]


def test_beat_entries_expire_before_next_run():
    for entry in app.conf.beat_schedule.values():
        assert 0 < entry["options"]["expires"] < entry["schedule"]


def test_retry_keeps_original_expiry():
    # self.retry 는 signature_from_request 로 다시 발행한다. 원래 만료 시각이 따라가므로
    # 다음 beat 주기를 넘는 재시도는 실행 전에 버려진다(celery_app.py 주석).
    request = Context(
        id="t",
        args=(),
        kwargs={},
        delivery_info={},
        expires="2026-11-18T00:04:00+00:00",
    )
    sig = process_retry_claim.signature_from_request(request, countdown=60)
    assert sig.options["expires"] == "2026-11-18T00:04:00+00:00"


def test_lock_key_is_stable_and_per_planet():
    # 프로세스가 달라도 같아야 한다(hash() 와 달리 고정값). 값이 바뀌면 배포 중 옛·새 파드가
    # 서로 다른 키를 잡아 막지 못한다.
    assert planet_nonce_lock_key(b"0x000000000000") == 5838264444796490752
    assert planet_nonce_lock_key(b"0x000000000000") == planet_nonce_lock_key(
        b"0x000000000000"
    )
    assert planet_nonce_lock_key(b"0x000000000000") != planet_nonce_lock_key(
        b"0x000000000001"
    )
    assert -(2**63) <= planet_nonce_lock_key(b"0x100000000003") < 2**63
