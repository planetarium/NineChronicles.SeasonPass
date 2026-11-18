"""Trackers add exp to the latest user_season_pass row under a row lock (real Postgres).

A concurrent /claim deducts the repeating reward (exp -= k*repeat, level = max).
The tracker used to read the row unlocked and write exp/level back as absolute
values, erasing that deduction so the same repeating reward could be claimed
again. HTTP (node/CDN) must still happen before the lock is taken (#308).

Needs Postgres: TRACKER_TEST_PG_DSN, or postgres:postgres@localhost/season_pass_tracker_test.
Skipped when the default DB is unreachable; never skipped when the env var is set (CI).
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from app.consumers import adventure_boss_consumer, courage_consumer
from app.utils import season_pass as season_pass_utils
from shared.enums import ActionType, PassType, PlanetID
from shared.models.action import ActionHistory, Block
from shared.models.base import Base
from shared.models.season_pass import Exp, Level, SeasonPass
from shared.models.user import UserSeasonPass
from shared.schemas.message import TrackerMessage
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

DSN = os.environ.get(
    "TRACKER_TEST_PG_DSN",
    "postgresql://postgres:postgres@localhost:5432/season_pass_tracker_test",
)
engine = create_engine(DSN)

PLANET = PlanetID.ODIN
AGENT = "0x" + "a" * 40
AVATAR = "0x" + "b" * 40
# 3 real levels + level 4 for the repeating reward (100 exp per repeat)
LEVELS = [(1, 0), (2, 100), (3, 200), (4, 300)]
ACTION_EXP = 10


@pytest.fixture
def db(monkeypatch):
    try:
        with engine.connect():
            pass
    except OperationalError:
        if "TRACKER_TEST_PG_DSN" in os.environ:
            raise
        pytest.skip(f"Postgres not reachable at {DSN}")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(courage_consumer, "engine", engine)
    monkeypatch.setattr(adventure_boss_consumer, "engine", engine)
    yield
    Base.metadata.drop_all(bind=engine)


def seed(pass_type: PassType, exp: int, level: int):
    now = datetime.now(tz=timezone.utc)
    with Session(engine) as s:
        s.add(
            SeasonPass(
                id=1,
                pass_type=pass_type,
                season_index=1,
                start_timestamp=now - timedelta(days=1),
                end_timestamp=now + timedelta(days=1),
                instant_exp=0,
                reward_list=[],
            )
        )
        s.add_all(
            Exp(season_pass_id=1, action_type=t, exp=ACTION_EXP) for t in ActionType
        )
        s.add_all(Level(pass_type=pass_type, level=l, exp=e) for l, e in LEVELS)
        s.add(Block(planet_id=PLANET, pass_type=pass_type, last_processed_index=0))
        s.add(
            UserSeasonPass(
                planet_id=PLANET,
                agent_addr=AGENT,
                avatar_addr=AVATAR,
                season_pass_id=1,
                exp=exp,
                level=level,
            )
        )
        s.commit()


def usp_row():
    with Session(engine) as s:
        u = s.scalar(select(UserSeasonPass))
        return u.exp, u.level


def processed_index(pass_type: PassType):
    with Session(engine) as s:
        return s.scalar(
            select(Block.last_processed_index).where(Block.pass_type == pass_type)
        )


def assert_usp_unlocked():
    """Called from inside mocked HTTP: no user_season_pass row lock may be held then."""
    with engine.connect() as c:
        c.execute(text("SET lock_timeout = '100ms'"))
        c.execute(text("SELECT id FROM user_season_pass FOR UPDATE NOWAIT"))
        c.rollback()


def wait_lock_waiters(n: int = 1, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as c:
            waiting = c.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
        if waiting >= n:
            return
        time.sleep(0.05)
    raise AssertionError("tracker never waited on the row lock")


def run_during_claim(consume, message):
    """Run the tracker while a /claim transaction holds the row and deducts 3 repeats."""
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text("SELECT id FROM user_season_pass FOR UPDATE"))
    # what create_claim does for 3 repeating rewards above max level 3
    conn.execute(
        text(
            "UPDATE user_season_pass SET exp = exp - 300, level = 3, last_normal_claim = 3"
        )
    )
    error = []

    def worker():
        try:
            consume(message)
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=worker)
    try:
        t.start()
        wait_lock_waiters()
    finally:
        trans.commit()
        conn.close()
    t.join(timeout=30)
    assert not error, error


def courage_message(action_data):
    return TrackerMessage(planet_id=PLANET.decode(), block=1, action_data=action_data)


def test_courage_keeps_concurrent_claim_deduction(db):
    seed(PassType.COURAGE_PASS, exp=550, level=4)
    run_during_claim(
        courage_consumer.consume_courage_message,
        courage_message(
            {
                "battle": [
                    {
                        "agent_addr": AGENT,
                        "avatar_addr": AVATAR,
                        "count_base": 1,
                        "tx_id": "ab" * 32,
                    }
                ]
            }
        ),
    )
    # 550 - 300 (claimed repeats) + 10 (arena); level recomputed from that
    assert usp_row() == (260, 3)
    assert processed_index(PassType.COURAGE_PASS) == 1


def test_adv_boss_keeps_concurrent_claim_deduction(db):
    seed(PassType.ADVENTURE_BOSS_PASS, exp=550, level=4)
    run_during_claim(
        adventure_boss_consumer.consume_adventure_boss_message,
        TrackerMessage(
            planet_id=PLANET.decode(),
            block=1,
            action_data={
                "wanted": [
                    {
                        "season_index": 1,
                        "agent_addr": AGENT,
                        "avatar_addr": AVATAR,
                        "count_base": 1,
                        "tx_id": "ab" * 32,
                    }
                ]
            },
        ),
    )
    assert usp_row() == (260, 3)
    assert processed_index(PassType.ADVENTURE_BOSS_PASS) == 1


def test_courage_sweep_http_runs_before_row_lock(db, monkeypatch):
    seed(PassType.COURAGE_PASS, exp=0, level=0)
    monkeypatch.setattr(courage_consumer.config, "headless_jwt_secret", "test-secret")
    courage_consumer.coef_dict.clear()

    def stake_state(*args, **kwargs):
        assert_usp_unlocked()
        return MagicMock(
            json=lambda: {"data": {"stateQuery": {"stakeState": {"deposit": "1"}}}}
        )

    def stage_cost(*args):
        assert_usp_unlocked()
        return 30

    with patch.object(courage_consumer, "ap_coef") as ap, patch.object(
        courage_consumer.requests, "post", side_effect=stake_state
    ) as post, patch.object(
        courage_consumer, "get_stage_cost_ap", side_effect=stage_cost
    ) as cost:
        ap.set_url.side_effect = lambda **kw: assert_usp_unlocked()
        ap.get_ap_coef.return_value = 100
        courage_consumer.consume_courage_message(
            courage_message(
                {
                    "hack_and_slash_sweep10": [
                        {
                            "agent_addr": AGENT,
                            "avatar_addr": AVATAR,
                            "count_base": 150,
                            "stage_id": 502,
                            "tx_id": "ab" * 32,
                        }
                    ]
                }
            )
        )
    assert (post.call_count, cost.call_count, ap.set_url.call_count) == (1, 1, 1)
    assert usp_row() == (50, 1)  # 150 AP // 30 = 5 plays x 10 exp
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(ActionHistory)) == 1


def test_adv_boss_floor_http_runs_before_row_lock(db, monkeypatch):
    seed(PassType.ADVENTURE_BOSS_PASS, exp=0, level=0)

    def floor(*args, **kwargs):
        assert_usp_unlocked()
        return 3

    monkeypatch.setattr(adventure_boss_consumer, "get_explore_floor", floor)
    adventure_boss_consumer.consume_adventure_boss_message(
        TrackerMessage(
            planet_id=PLANET.decode(),
            block=1,
            action_data={
                "explore_adventure_boss": [
                    {
                        "season_index": 1,
                        "agent_addr": AGENT,
                        "avatar_addr": AVATAR,
                        "count_base": 0,
                        "tx_id": "ab" * 32,
                    }
                ]
            },
        ),
    )
    # first explore: AP_PER_ACTION(2) * min(floor 3 + 1, 5) = 8 -> 80 exp
    assert usp_row() == (80, 1)


def test_lock_wait_timeout_leaves_block_unprocessed(db, monkeypatch):
    seed(PassType.COURAGE_PASS, exp=550, level=4)
    monkeypatch.setattr(season_pass_utils, "USP_LOCK_TIMEOUT", "200ms")
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text("SELECT id FROM user_season_pass FOR UPDATE"))
    try:
        with pytest.raises(OperationalError):
            courage_consumer.consume_courage_message(
                courage_message(
                    {
                        "battle": [
                            {
                                "agent_addr": AGENT,
                                "avatar_addr": AVATAR,
                                "count_base": 1,
                                "tx_id": "ab" * 32,
                            }
                        ]
                    }
                )
            )
    finally:
        trans.rollback()
        conn.close()
    # retried on the next cycle from the same block
    assert processed_index(PassType.COURAGE_PASS) == 0
    assert usp_row() == (550, 4)
