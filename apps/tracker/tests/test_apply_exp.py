"""apply_exp: exp accumulates by count_base and the level follows the Level table thresholds."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.utils import exp as exp_module
from app.utils import season_pass as season_pass_module
from shared.enums import ActionType, PlanetID

LEVELS = {1: 0, 2: 100, 3: 300, 4: 600}


def user(exp=0, level=0):
    return SimpleNamespace(
        exp=exp,
        level=level,
        season_pass_id=9,
        agent_addr="0xagent",
        avatar_addr="0xavatar",
    )


@pytest.mark.parametrize(
    "apply_exp", [exp_module.apply_exp, season_pass_module.apply_exp]
)
@pytest.mark.parametrize(
    "start,count,expected_exp,expected_level",
    [
        (0, 1, 50, 1),  # below the 2nd threshold
        (50, 1, 100, 2),  # exactly on a threshold
        (0, 6, 300, 3),  # count multiplies the per-action exp
        (590, 1, 640, 4),  # top level
        (0, 0, 0, 1),  # zero count still sets level from the table
    ],
)
def test_exp_and_level(apply_exp, start, count, expected_exp, expected_level):
    sess = MagicMock()
    u = user(exp=start)
    apply_exp(
        sess,
        PlanetID.ODIN,
        {"0xavatar": u},
        ActionType.HAS,
        50,
        LEVELS,
        1000,
        [{"avatar_addr": "0xavatar", "count_base": count, "tx_id": "ab" * 32}],
    )
    assert (u.exp, u.level) == (expected_exp, expected_level)
    history = sess.add.call_args.args[0]
    assert (history.count, history.exp, history.block_index, history.season_id) == (
        count,
        50 * count,
        1000,
        9,
    )


def test_history_tx_id_default_in_exp_module():
    sess = MagicMock()
    exp_module.apply_exp(
        sess,
        PlanetID.ODIN,
        {"0xavatar": user()},
        ActionType.ARENA,
        10,
        LEVELS,
        1,
        [{"avatar_addr": "0xavatar", "count_base": 1}],
    )
    assert sess.add.call_args.args[0].tx_id == "0" * 64


def test_multiple_actions_same_avatar_accumulate():
    sess = MagicMock()
    u = user()
    season_pass_module.apply_exp(
        sess,
        PlanetID.ODIN,
        {"0xavatar": u},
        ActionType.RAID,
        60,
        LEVELS,
        1,
        [{"avatar_addr": "0xavatar", "count_base": 1}] * 5,
    )
    assert (u.exp, u.level, sess.add.call_count) == (300, 3, 5)
