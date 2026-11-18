"""consume_claim_message — 실패 로그에 어느 플래닛인지 남긴다.

process_claim 의 실패 로그("Error processing claim message")에는 uuid 만 있어, 한 플래닛만 멈췄을 때
로그로 플래닛을 가려내지 못했다.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.consumers import claim_consumer
from shared.schemas.message import ClaimMessage

HEIMDALL = b"0x000000000001"


def run_consume(stage_result=(False, "node rejected", None), next_nonce=7):
    claim = SimpleNamespace(
        uuid="u-1",
        tx=None,
        tx_id=None,
        tx_status=None,
        planet_id=HEIMDALL,
        nonce=None,
        normal_levels=[1],
        premium_levels=[],
        season_pass=SimpleNamespace(pass_type=SimpleNamespace(value="CouragePass")),
        avatar_addr="0xavatar",
        reward_list=[],
    )
    sess = MagicMock()
    sess.scalars.return_value = [claim]
    sess.scalar.return_value = 3
    gql = MagicMock()
    gql.get_next_nonce.side_effect = (
        next_nonce if isinstance(next_nonce, Exception) else [next_nonce]
    )
    gql.stage.return_value = stage_result
    account = MagicMock(pubkey=b"\x02", address="0xsigner")
    with patch.object(
        claim_consumer, "scoped_session", return_value=sess
    ), patch.object(claim_consumer, "_get_account", return_value=account), patch.object(
        claim_consumer, "GQLClient", return_value=gql
    ), patch.object(
        claim_consumer, "build_claim_memo", return_value="memo"
    ), patch.object(
        claim_consumer, "create_grant_items_unsigned_tx", return_value=b"\x00"
    ), patch.object(
        claim_consumer, "create_signed_tx", return_value=b"\x01"
    ), patch.object(
        claim_consumer, "logger"
    ) as logger:
        with pytest.raises(Exception) as excinfo:
            claim_consumer.consume_claim_message(ClaimMessage(uuid="u-1"))
    logger.raised = excinfo.value
    return logger


def timing_call(logger):
    return next(c for c in logger.info.call_args_list if c.args[0] == "claim_timing")


def test_stage_failure_log_has_planet_id():
    logger = run_consume()

    stage_error = logger.error.call_args
    assert stage_error.args[0].startswith("Failed to stage tx with nonce 7")
    assert stage_error.kwargs == {"planet_id": HEIMDALL, "nonce": 7}
    assert timing_call(logger).kwargs["planet_id"] == HEIMDALL


def test_failure_before_stage_still_tells_planet():
    # nonce 조회(노드 연결) 실패처럼 consumer 가 따로 로그를 남기지 않는 실패도 claim_timing 에 남는다.
    logger = run_consume(next_nonce=ConnectionError("heimdall-rpc down"))

    timing = timing_call(logger)
    assert timing.kwargs["outcome"] == "error"
    assert timing.kwargs["planet_id"] == HEIMDALL


def test_timing_log_has_uuid():
    # concurrency=1 이어도 process_claim 실패 로그(uuid)와 claim_timing 을 확실히 잇는 키.
    logger = run_consume()

    assert timing_call(logger).kwargs["uuid"] == "u-1"


def test_raised_error_carries_planet_id():
    # process_claim 이 실패 로그에 플래닛을 붙일 수 있게 예외에 실어 보낸다.
    logger = run_consume(next_nonce=ConnectionError("heimdall-rpc down"))

    assert isinstance(logger.raised, ConnectionError)
    assert logger.raised.planet_id == HEIMDALL
