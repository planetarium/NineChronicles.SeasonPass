"""process_retry_claim — 한 플래닛의 실패가 다른 플래닛 클레임 처리를 막지 않는다.

2026-11-17 인터널: 맨 앞의 thor 클레임(노드 DNS 실패)에서 묶음 전체가 재시도로 돌아가
그 뒤의 오딘 클레임 10건이 다시 만들어지지 못했다.
"""
from unittest.mock import MagicMock, patch

from app.tasks import claim_task

THOR = b"0x100000000003"
ODIN = b"0x100000000000"


def run_retry(rows, consume):
    sess = MagicMock()
    query = sess.query.return_value.filter.return_value.order_by.return_value
    query.limit.return_value.all.return_value = rows
    with patch.object(claim_task, "scoped_session", return_value=sess), patch.object(
        claim_task, "consume_claim_message", side_effect=consume
    ) as consume_mock, patch.object(claim_task, "logger"), patch.object(
        claim_task.process_retry_claim, "retry"
    ) as retry:
        claim_task.process_retry_claim.run()
    return [c.args[0].uuid for c in consume_mock.call_args_list], retry, sess


def fail_thor(message):
    if message.uuid.startswith("thor"):
        raise ConnectionError("validator-5.thor: Name resolution failed")


def test_failed_planet_does_not_block_other_planets():
    called, retry, _ = run_retry(
        [("thor-1", THOR), ("odin-1", ODIN), ("odin-2", ODIN)], fail_thor
    )

    assert called == ["thor-1", "odin-1", "odin-2"]
    retry.assert_not_called()


def test_rest_of_failed_planet_is_skipped_this_round():
    # stage 실패가 이어질 때 같은 플래닛에 nonce 가 줄줄이 할당되지 않게 한다.
    called, _, _ = run_retry(
        [("thor-1", THOR), ("odin-1", ODIN), ("thor-2", THOR)], fail_thor
    )

    assert called == ["thor-1", "odin-1"]


def test_query_only_selects_configured_planets(monkeypatch):
    monkeypatch.setattr(
        claim_task.config, "gql_url_map", {ODIN.decode(): "http://odin"}
    )

    _, _, sess = run_retry([], fail_thor)

    criteria = sess.query.return_value.filter.call_args.args
    planet_filter = [c for c in criteria if "planet_id" in str(c)]
    assert len(planet_filter) == 1
    assert planet_filter[0].right.value == [ODIN]
