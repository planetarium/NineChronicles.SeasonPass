"""process_claim — 실패 로그("Error processing claim message")에 플래닛을 남긴다.

예전엔 uuid(message)만 있어 한 플래닛만 멈췄을 때 로그로 플래닛을 가려내지 못했다.
"""
from unittest.mock import patch

from app.tasks import claim_task

HEIMDALL = b"0x000000000001"


def run_process_claim(exc):
    def consume(message):
        raise exc

    with patch.object(
        claim_task, "consume_claim_message", side_effect=consume
    ), patch.object(claim_task, "logger") as logger, patch.object(
        claim_task.process_claim, "retry"
    ) as retry:
        claim_task.process_claim.run({"uuid": "u-1"})
    retry.assert_called_once_with(exc=exc)
    return next(
        c
        for c in logger.error.call_args_list
        if c.args[0] == "Error processing claim message"
    )


def test_failure_log_has_planet_id():
    exc = ConnectionError("heimdall-rpc down")
    exc.planet_id = HEIMDALL

    log = run_process_claim(exc)

    assert log.kwargs["planet_id"] == HEIMDALL
    assert log.kwargs["message"] == {"uuid": "u-1"}


def test_failure_log_without_planet_id():
    # claim 을 읽기 전에 실패하면 플래닛을 모른다.
    log = run_process_claim(RuntimeError("db down"))

    assert log.kwargs["planet_id"] is None
