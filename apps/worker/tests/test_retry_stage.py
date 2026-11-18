"""process_retry_stage — 한 플래닛의 stage 실패가 다른 플래닛 재스테이징을 막지 않는다.

예전엔 모든 플래닛을 nonce 순으로 100건 고르고 첫 실패에서 묶음 전체를 재시도해, 멈춘 오딘
(2026-11-16 INVALID 51건)의 낮은 nonce 건이 하임달 INVALID 건의 재스테이징을 막을 수 있었다.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from shared.enums import TxStatus
from sqlalchemy.dialects import postgresql

from app.tasks import stage_task

ODIN = b"0x000000000000"
HEIMDALL = b"0x000000000001"


def claim(planet_id, nonce):
    return SimpleNamespace(
        id=hash((planet_id, nonce)) % 10_000,
        planet_id=planet_id,
        nonce=nonce,
        tx="00",
        tx_id=f"tx-{nonce}",
        tx_status=TxStatus.INVALID,
    )


def staged_ids(sess):
    """restage 가 STAGED 로 적은 claim id(조건부 UPDATE 의 WHERE id)."""
    ids = []
    for call in sess.execute.call_args_list:
        params = call.args[0].compile(dialect=postgresql.dialect()).params
        if params.get("tx_status") == TxStatus.STAGED:
            ids.append(params["id_1"])
    return ids


def run_retry_stage(rows_by_planet, stage):
    sess = MagicMock()
    query = sess.query.return_value.filter.return_value.order_by.return_value
    query.limit.return_value.all.side_effect = list(rows_by_planet.values())
    gql = MagicMock()
    gql.stage.side_effect = stage
    with patch.object(stage_task, "scoped_session", return_value=sess), patch.object(
        stage_task, "GQLClient", return_value=gql
    ), patch.object(
        stage_task, "configured_planet_ids", return_value=list(rows_by_planet)
    ), patch.object(
        stage_task, "logger"
    ), patch.object(
        stage_task.process_retry_stage, "retry"
    ) as retry:
        stage_task.process_retry_stage.run()
    return gql, retry, sess


def fail_odin(planet_id, tx):
    if planet_id == ODIN:
        return False, "odin node down", None
    return True, "", "txid"


def test_failed_planet_does_not_block_other_planets():
    odin = [claim(ODIN, 1), claim(ODIN, 2)]
    heimdall = [claim(HEIMDALL, 7), claim(HEIMDALL, 8)]

    gql, retry, sess = run_retry_stage({ODIN: odin, HEIMDALL: heimdall}, fail_odin)

    # 오딘은 첫 건 실패 뒤 남은 건을 건너뛰고(nonce 순서라 뒤를 보내도 소용없다), 하임달은 모두 보낸다.
    assert [c.args[0] for c in gql.stage.call_args_list] == [ODIN, HEIMDALL, HEIMDALL]
    assert staged_ids(sess) == [c.id for c in heimdall]
    retry.assert_not_called()


def test_stage_exception_is_treated_as_planet_failure():
    def raise_odin(planet_id, tx):
        if planet_id == ODIN:
            raise ConnectionError("validator-5.odin: Name resolution failed")
        return True, "", "txid"

    heimdall = [claim(HEIMDALL, 7)]

    gql, retry, sess = run_retry_stage(
        {ODIN: [claim(ODIN, 1)], HEIMDALL: heimdall}, raise_odin
    )

    assert staged_ids(sess) == [heimdall[0].id]
    retry.assert_not_called()


def test_each_planet_is_queried_separately():
    _, _, sess = run_retry_stage({ODIN: [], HEIMDALL: []}, fail_odin)

    planets = [
        [c.right.value for c in call.args if "planet_id" in str(c)]
        for call in sess.query.return_value.filter.call_args_list
    ]
    assert planets == [[ODIN], [HEIMDALL]]


def test_unexpected_error_names_the_planet():
    # 플래닛 조회 자체가 예외로 끝나면(DB 오류 등) 어느 플래닛에서 멈췄는지 로그에 남긴다.
    def restage(sess, gql, planet_id, now, skip_expired):
        if planet_id == HEIMDALL:
            raise RuntimeError("db gone")
        return True, 0

    with patch.object(stage_task, "scoped_session"), patch.object(
        stage_task, "GQLClient"
    ), patch.object(
        stage_task, "configured_planet_ids", return_value=[ODIN, HEIMDALL]
    ), patch.object(
        stage_task, "_restage_planet", side_effect=restage
    ), patch.object(
        stage_task, "logger"
    ) as logger, patch.object(
        stage_task.process_retry_stage, "retry"
    ) as retry:
        stage_task.process_retry_stage.run()

    retry.assert_called_once()
    call = logger.error.call_args
    assert call.args[0] == "Error processing retry stage"
    assert call.kwargs["planet_id"] == HEIMDALL
