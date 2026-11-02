"""handle_sweep: a sweep reports used AP; real play count = used AP // (stage CostAP × stake coefficient%)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.consumers import courage_consumer
from shared.enums import ActionType, PlanetID

LEVELS = {1: 0, 2: 100, 3: 1000}


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(courage_consumer.config, "headless_jwt_secret", "test-secret")

    def _run(count_base, stage_id, cost_ap, coef, deposit="1000"):
        courage_consumer.coef_dict.clear()
        sess = MagicMock()
        u = SimpleNamespace(
            exp=0,
            level=0,
            season_pass_id=1,
            agent_addr="0xagent",
            avatar_addr="0xavatar",
        )
        stake = MagicMock(
            json=lambda: {
                "data": {
                    "stateQuery": {
                        "stakeState": None if deposit is None else {"deposit": deposit}
                    }
                }
            }
        )
        with patch.object(courage_consumer, "ap_coef") as ap, patch.object(
            courage_consumer.requests, "post", return_value=stake
        ), patch.object(
            courage_consumer, "get_stage_cost_ap", return_value=cost_ap
        ) as cost:
            ap.get_ap_coef.return_value = coef
            courage_consumer.handle_sweep(
                sess,
                PlanetID.ODIN,
                {"0xavatar": u},
                10,
                LEVELS,
                1,
                [
                    {
                        "agent_addr": "0xagent",
                        "avatar_addr": "0xavatar",
                        "count_base": count_base,
                        "stage_id": stage_id,
                    }
                ],
            )
        return u, sess, cost

    return _run


@pytest.mark.parametrize(
    "count_base,stage_id,cost_ap,coef,plays",
    [
        (150, 502, 30, 100, 5),  # 150 AP / 30 per play
        (150, 502, 30, 60, 8),  # staking 60%: 150 // 18 = 8
        (149, 502, 30, 100, 4),  # floored
        (25, None, None, 100, 5),  # no stage id → 5 AP per adventure
    ],
)
def test_real_count(run, count_base, stage_id, cost_ap, coef, plays):
    u, sess, cost = run(count_base, stage_id, cost_ap, coef)
    assert u.exp == 10 * plays
    history = sess.add.call_args.args[0]
    assert (history.action, history.count, history.exp) == (
        ActionType.SWEEP,
        plays,
        10 * plays,
    )
    if stage_id is None:
        cost.assert_not_called()


def test_no_stake_state_means_full_cost(run):
    u, _, _ = run(
        150, 502, 30, coef=60, deposit=None
    )  # coefficient lookup skipped → 100
    assert u.exp == 10 * 5


def test_negative_count_is_reported_and_skipped(run):
    u, sess, _ = run(-150, 502, 30, 100)
    assert u.exp == 0
    sess.add.assert_not_called()
