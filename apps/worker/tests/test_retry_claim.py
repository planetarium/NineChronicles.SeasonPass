"""process_retry_claim — 한 플래닛이 다른 플래닛 클레임 재처리를 막지 않는다.

2026-11-17 인터널: 맨 앞의 thor 클레임(노드 DNS 실패)에서 묶음 전체가 재시도로 돌아가
그 뒤의 오딘 클레임 10건이 다시 만들어지지 못했다. 또 모든 플래닛을 created_at 순으로 100건
골라, 한 플래닛에 오래된 건이 100건 넘게 쌓이면 다른 플래닛 건은 창에 들지 못했다.
"""
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tasks import claim_task

THOR = b"0x100000000003"
ODIN = b"0x100000000000"

Row = namedtuple("Row", "uuid planet_id")


def run_retry(rows_by_planet, consume):
    sess = MagicMock()
    query = sess.query.return_value.filter.return_value.order_by.return_value
    query.limit.return_value.all.side_effect = [
        [Row(uuid, planet_id) for uuid in rows]
        for planet_id, rows in rows_by_planet.items()
    ]
    with patch.object(claim_task, "scoped_session", return_value=sess), patch.object(
        claim_task, "configured_planet_ids", return_value=list(rows_by_planet)
    ), patch.object(
        claim_task, "consume_claim_message", side_effect=consume
    ) as consume_mock, patch.object(
        claim_task, "logger"
    ) as logger, patch.object(
        claim_task.process_retry_claim, "retry"
    ) as retry:
        claim_task.process_retry_claim.run()
    return [c.args[0].uuid for c in consume_mock.call_args_list], retry, sess, logger


def fail_thor(message):
    if message.uuid.startswith("thor"):
        raise ConnectionError("validator-5.thor: Name resolution failed")


def test_failed_planet_does_not_block_other_planets():
    called, retry, _, _ = run_retry(
        {THOR: ["thor-1"], ODIN: ["odin-1", "odin-2"]}, fail_thor
    )

    assert called == ["thor-1", "odin-1", "odin-2"]
    retry.assert_not_called()


def test_rest_of_failed_planet_is_skipped_this_round():
    # stage 실패가 이어질 때 같은 플래닛에 nonce 가 줄줄이 할당되지 않게 한다.
    called, _, _, _ = run_retry(
        {THOR: ["thor-1", "thor-2"], ODIN: ["odin-1"]}, fail_thor
    )

    assert called == ["thor-1", "odin-1"]


def test_each_planet_is_queried_separately():
    _, _, sess, _ = run_retry({THOR: [], ODIN: []}, fail_thor)

    planets = [
        [c.right.value for c in call.args if "planet_id" in str(c)]
        for call in sess.query.return_value.filter.call_args_list
    ]
    assert planets == [[THOR], [ODIN]]
    query = sess.query.return_value.filter.return_value.order_by.return_value
    assert [c.args for c in query.limit.call_args_list] == [(100,), (100,)]


def test_full_window_on_one_planet_does_not_starve_others():
    # 오딘 오래된 건이 100건이어도 thor 건은 같은 차례에 처리된다.
    odin = [f"odin-{i}" for i in range(100)]

    called, _, _, _ = run_retry({ODIN: odin, THOR: ["thor-ok"]}, lambda m: None)

    assert "thor-ok" in called
    assert len(called) == 101


def test_failure_log_has_planet_id():
    _, _, _, logger = run_retry({THOR: ["thor-1"], ODIN: ["odin-1"]}, fail_thor)

    per_claim = [
        c for c in logger.error.call_args_list if c.args[0] == "Error retrying claim"
    ]
    assert [c.kwargs["planet_id"] for c in per_claim] == [THOR]
    summary = [
        c
        for c in logger.error.call_args_list
        if c.args[0] == "Retry claim finished with failures"
    ]
    assert summary[0].kwargs["planets"] == [THOR]


def test_read_transaction_is_closed_before_processing():
    # 고른 뒤 처리하는 동안 조회 트랜잭션을 열어 두지 않는다(idle-in-transaction).
    # 처리는 consume_claim_message 가 자기 세션으로 한다.
    events = []
    sess = MagicMock()
    sess.commit.side_effect = lambda: events.append("commit")
    query = sess.query.return_value.filter.return_value.order_by.return_value
    query.limit.return_value.all.side_effect = [
        [Row("thor-1", THOR), Row("thor-2", THOR)],
        [Row("odin-1", ODIN)],
    ]
    with patch.object(claim_task, "scoped_session", return_value=sess), patch.object(
        claim_task, "configured_planet_ids", return_value=[THOR, ODIN]
    ), patch.object(
        claim_task,
        "consume_claim_message",
        side_effect=lambda m: events.append(m.uuid),
    ), patch.object(
        claim_task.process_retry_claim, "retry"
    ):
        claim_task.process_retry_claim.run()

    # 모든 플래닛을 고른 뒤 한 번 commit 하고, 플래닛을 번갈아 처리한다.
    assert events == ["commit", "thor-1", "odin-1", "thor-2"]
    sess.close.assert_called_once()


def test_configured_planet_ids_follow_gql_url_map(monkeypatch):
    monkeypatch.setattr(
        claim_task.config, "gql_url_map", {ODIN.decode(): "http://odin"}
    )

    assert claim_task.configured_planet_ids() == [ODIN]


HEIMDALL = b"0x100000000001"


def test_planets_are_processed_round_robin():
    # 한 플래닛의 100건을 다 처리한 뒤 다음 플래닛으로 가면, 시간 상한에 걸릴 때 뒤 플래닛이 매번 잘린다.
    called, _, _, _ = run_retry(
        {
            ODIN: ["odin-1", "odin-2", "odin-3"],
            HEIMDALL: ["hd-1"],
            THOR: ["thor-1", "thor-2"],
        },
        lambda m: None,
    )

    assert called == ["odin-1", "hd-1", "thor-1", "odin-2", "thor-2", "odin-3"]


def test_stops_starting_new_claims_after_time_budget(monkeypatch):
    # 한 번 실행이 beat expires(240초)·종료 유예(180초)를 넘지 않게 상한 뒤엔 새 claim 을 시작하지 않는다.
    # 건마다 50초가 걸린다고 치면 0·50·100초에 시작한 세 건까지만 처리하고 150초에는 시작하지 않는다.
    clock = [0]
    monkeypatch.setattr(claim_task, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def consume(message):
        clock[0] += 50

    called, retry, _, logger = run_retry(
        {
            ODIN: ["odin-1", "odin-2", "odin-3", "odin-4"],
            HEIMDALL: ["hd-1", "hd-2", "hd-3"],
        },
        consume,
    )

    assert called == ["odin-1", "hd-1", "odin-2"]
    retry.assert_not_called()
    assert any(
        c.args[0] == "Retry claim stopped at time budget"
        for c in logger.warning.call_args_list
    )


def fail_all(message):
    raise ConnectionError("node down")


def test_all_planets_failed_stops_early():
    called, retry, _, _ = run_retry(
        {THOR: ["thor-1", "thor-2"], ODIN: ["odin-1", "odin-2"]}, fail_all
    )

    assert called == ["thor-1", "odin-1"]
    retry.assert_not_called()
