"""/claim-prev·/upgrade lock the user_season_pass row like /claim (real Postgres).

Each test holds the row lock from a third connection, fires two requests that
both reach the row, then releases it. Without the row lock both requests read
the same state and each creates a reward Claim.
"""
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from shared.enums import PassType, PlanetID
from shared.models.season_pass import Level, SeasonPass
from shared.models.user import Claim, UserSeasonPass
from sqlalchemy import func, select, text

from app.api import user as user_api
from app.exceptions import InvalidUpgradeRequestError, ServerOverloadError
from app.schemas.user import ClaimRequestSchema, UpgradeRequestSchema
from tests.conftest import TestingSessionLocal, engine

AGENT = "0x" + "a" * 40
AVATAR = "0x" + "b" * 40
# 3 real levels + level 4 for the repeating reward (100 exp per repeat)
LEVELS = [(1, 0), (2, 100), (3, 200), (4, 300)]
REWARDS = [
    {
        "level": lvl,
        "normal": [{"ticker": "CRYSTAL", "amount": 10}],
        "premium": [{"ticker": "CRYSTAL", "amount": 100}],
    }
    for lvl, _ in LEVELS
]


def seed(prev: bool, usp: dict | None):
    now = datetime.now(tz=timezone.utc)
    start, end = (
        (now - timedelta(days=10), now - timedelta(days=1))
        if prev
        else (now - timedelta(days=1), now + timedelta(days=1))
    )
    with TestingSessionLocal() as s:
        s.add(
            SeasonPass(
                id=1,
                pass_type=PassType.COURAGE_PASS,
                season_index=1,
                start_timestamp=start,
                end_timestamp=end,
                instant_exp=150,
                reward_list=REWARDS,
            )
        )
        s.add_all(
            Level(pass_type=PassType.COURAGE_PASS, level=lvl, exp=exp)
            for lvl, exp in LEVELS
        )
        if usp is not None:
            s.add(
                UserSeasonPass(
                    planet_id=PlanetID.ODIN,
                    agent_addr=AGENT,
                    avatar_addr=AVATAR,
                    season_pass_id=1,
                    **usp,
                )
            )
        s.commit()


def hold_usp_lock():
    """Lock the user_season_pass row from another connection, as a racing writer would."""
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text("SELECT id FROM user_season_pass FOR UPDATE"))
    return conn, trans


def wait_lock_waiters(n: int, timeout: float = 10):
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
    raise AssertionError(f"expected {n} requests waiting on the row lock")


def run_concurrently(call, n: int = 2):
    """Run `call(sess)` n times in parallel while the row is locked, then release it."""
    results = [None] * n
    conn, trans = hold_usp_lock()

    def worker(i):
        sess = TestingSessionLocal()
        try:
            results[i] = call(sess)
        except Exception as e:  # collected for assertions
            results[i] = e
        finally:
            sess.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    try:
        for t in threads:
            t.start()
        wait_lock_waiters(n)
    finally:
        trans.commit()
        conn.close()
    for t in threads:
        t.join(timeout=30)
    return results


def reward_claims():
    with TestingSessionLocal() as s:
        return s.scalar(
            select(func.count()).select_from(Claim).where(Claim.reward_list != [])
        )


@pytest.fixture
def send_to_worker():
    with patch.object(user_api, "send_to_worker", return_value="task") as m:
        yield m


def test_claim_prev_double_request_creates_one_claim(test_db, send_to_worker):
    seed(prev=True, usp=dict(is_premium=True, level=3, exp=250))
    req = ClaimRequestSchema(
        planet_id=PlanetID.ODIN,
        agent_addr=AGENT,
        avatar_addr=AVATAR,
        pass_type=PassType.COURAGE_PASS,
        season_index=1,
        prev=True,
    )

    results = run_concurrently(lambda s: user_api.claim_prev_reward(req, sess=s))

    assert not [r for r in results if isinstance(r, Exception)]
    assert reward_claims() == 1
    assert send_to_worker.call_count == 1
    with TestingSessionLocal() as s:
        usp = s.scalar(select(UserSeasonPass))
        assert (usp.last_normal_claim, usp.last_premium_claim) == (3, 3)
    # the second request found nothing left: no empty Claim row either
    with TestingSessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Claim)) == 1
    assert sorted(len(r.reward_list) for r in results) == [0, 1]


def test_claim_prev_with_nothing_left_creates_no_claim(test_db, send_to_worker):
    """Like /claim: nothing to reward returns at once, without an empty Claim row."""
    seed(
        prev=True,
        usp=dict(
            is_premium=True, level=3, exp=250, last_normal_claim=3, last_premium_claim=3
        ),
    )
    req = ClaimRequestSchema(
        planet_id=PlanetID.ODIN,
        agent_addr=AGENT,
        avatar_addr=AVATAR,
        pass_type=PassType.COURAGE_PASS,
        season_index=1,
        prev=True,
    )

    with TestingSessionLocal() as s:
        result = user_api.claim_prev_reward(req, sess=s)
        assert result.reward_list == []
        assert result.user.level == 3

    with TestingSessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Claim)) == 0
    send_to_worker.assert_not_called()


def upgrade_request(**kwargs):
    return UpgradeRequestSchema(
        planet_id=PlanetID.ODIN,
        agent_addr=AGENT,
        avatar_addr=AVATAR,
        pass_type=PassType.COURAGE_PASS,
        season_index=1,
        g_sku="g",
        a_sku="a",
        reward_list=[{"ticker": "CRYSTAL", "amount": 1000, "decimal_places": 0}],
        **kwargs,
    )


def test_upgrade_double_request_creates_one_claim(test_db, send_to_worker):
    seed(prev=False, usp=dict(is_premium=False, level=1, exp=50))
    req = upgrade_request(is_premium=True)

    results = run_concurrently(lambda s: user_api.upgrade_season_pass(req, sess=s))

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], InvalidUpgradeRequestError)
    # a rejection IAP recognises as "nothing granted" (SEASON_PASS_REJECTED_BEFORE_GRANT)
    assert "doesn't request premium" in str(errors[0])
    assert reward_claims() == 1


def test_upgrade_premium_plus_exp_not_lost_to_concurrent_write(test_db, send_to_worker):
    """instant_exp is added to the exp committed by the racing writer, not to a stale read."""
    seed(prev=False, usp=dict(is_premium=True, level=1, exp=50))
    conn, trans = hold_usp_lock()
    result = {}

    def worker():
        sess = TestingSessionLocal()
        try:
            result["usp"] = user_api.upgrade_season_pass(
                upgrade_request(is_premium_plus=True), sess=sess
            )
            result["exp"] = result["usp"].exp
        finally:
            sess.close()

    t = threading.Thread(target=worker)
    try:
        t.start()
        wait_lock_waiters(1)
        # e.g. the courage tracker adding 100 exp while the upgrade waits
        conn.execute(text("UPDATE user_season_pass SET exp = exp + 100"))
    finally:
        trans.commit()
        conn.close()
    t.join(timeout=30)

    assert result["exp"] == 50 + 100 + 150


def test_upgrade_creates_missing_row_and_applies(test_db, send_to_worker):
    seed(prev=False, usp=None)

    sess = TestingSessionLocal()
    try:
        usp = user_api.upgrade_season_pass(
            upgrade_request(is_premium=True, is_premium_plus=True), sess=sess
        )
        assert (usp.is_premium, usp.is_premium_plus, usp.exp, usp.level) == (
            True,
            True,
            150,
            2,
        )
    finally:
        sess.close()
    assert reward_claims() == 1


def test_claim_lock_wait_times_out_as_overload(test_db, send_to_worker, monkeypatch):
    seed(prev=False, usp=dict(is_premium=True, level=3, exp=250))
    monkeypatch.setattr(user_api, "USP_LOCK_TIMEOUT", "200ms")
    req = ClaimRequestSchema(
        planet_id=PlanetID.ODIN,
        agent_addr=AGENT,
        avatar_addr=AVATAR,
        pass_type=PassType.COURAGE_PASS,
        season_index=1,
    )
    conn, trans = hold_usp_lock()
    sess = TestingSessionLocal()
    try:
        with pytest.raises(ServerOverloadError) as exc_info:
            user_api.claim_reward(req, sess=sess)
    finally:
        sess.close()
        trans.rollback()
        conn.close()
    assert reward_claims() == 0
    # 50건 제한 503 과 구별되고, 어느 행이 막혔는지 로그로 알 수 있어야 한다.
    assert exc_info.value.log_context == {
        "reason": "usp_lock_timeout",
        "endpoint": "claim",
        "planet_id": PlanetID.ODIN,
        "avatar_addr": AVATAR,
    }
