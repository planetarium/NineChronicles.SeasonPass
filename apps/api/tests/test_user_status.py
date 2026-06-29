"""Regression tests for the WorldClearPass exp/level backfill on the status
endpoints (`/api/user/status`, `/api/user/status/all`).

These guard the behaviour of the on-read backfill after it was reordered so the
`get_level()` SELECT runs *before* `target` is mutated (so SQLAlchemy autoflush
no longer takes the user_season_pass row lock across the rest of the block). The
reorder is behaviour-preserving; these tests pin that behaviour: a zero-exp
WorldClear row is backfilled to the RPC-reported cleared stage, the level is
derived from the level table, the value is persisted, and a non-zero row is left
untouched (and does not hit the RPC).
"""
from datetime import datetime, timedelta, timezone

from shared.enums import PassType, PlanetID
from shared.models.season_pass import Level
from shared.models.user import SeasonPass, UserSeasonPass

ODIN = "0x000000000000"
AGENT = "0xabc0000000000000000000000000000000000001"
AVATAR = "0xabc0000000000000000000000000000000000002"


def _seed_worldclear(test_session, *, exp=0, level=0, season_index=1):
    season = SeasonPass(
        pass_type=PassType.WORLD_CLEAR_PASS,
        season_index=season_index,
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
        instant_exp=0,
        reward_list=[],
    )
    test_session.add(season)
    test_session.flush()

    # level thresholds: exp>=10 -> L1, >=20 -> L2, >=30 -> L3
    for lv, threshold in ((1, 10), (2, 20), (3, 30)):
        test_session.add(
            Level(pass_type=PassType.WORLD_CLEAR_PASS, level=lv, exp=threshold)
        )

    usp = UserSeasonPass(
        planet_id=PlanetID.ODIN,
        agent_addr=AGENT,
        avatar_addr=AVATAR,
        season_pass_id=season.id,
        exp=exp,
        level=level,
    )
    test_session.add(usp)
    test_session.commit()
    return season, usp


def _reload_usp(test_session, season_id):
    test_session.expire_all()
    return (
        test_session.query(UserSeasonPass)
        .filter(
            UserSeasonPass.season_pass_id == season_id,
            UserSeasonPass.avatar_addr == AVATAR,
        )
        .one()
    )


def test_status_worldclear_backfills_exp_and_level_when_zero(
    client, test_session, monkeypatch
):
    season, _ = _seed_worldclear(test_session, exp=0, level=0)

    calls = []

    def fake_fetch(planet_id, avatar_addr):
        calls.append((planet_id, avatar_addr))
        return 25  # cleared stage; with the seeded levels this maps to L2

    monkeypatch.setattr("app.api.user._fetch_cleared_stage", fake_fetch)

    resp = client.get(
        "/api/user/status",
        params={
            "planet_id": ODIN,
            "agent_addr": AGENT,
            "avatar_addr": AVATAR,
            "pass_type": PassType.WORLD_CLEAR_PASS.value,
            "season_index": season.season_index,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exp"] == 25
    assert body["level"] == 2
    assert len(calls) == 1  # RPC hit exactly once

    persisted = _reload_usp(test_session, season.id)
    assert persisted.exp == 25
    assert persisted.level == 2


def test_status_worldclear_no_backfill_when_exp_nonzero(
    client, test_session, monkeypatch
):
    season, _ = _seed_worldclear(test_session, exp=15, level=1)

    calls = []
    monkeypatch.setattr(
        "app.api.user._fetch_cleared_stage",
        lambda planet_id, avatar_addr: calls.append(1) or 999,
    )

    resp = client.get(
        "/api/user/status",
        params={
            "planet_id": ODIN,
            "agent_addr": AGENT,
            "avatar_addr": AVATAR,
            "pass_type": PassType.WORLD_CLEAR_PASS.value,
            "season_index": season.season_index,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exp"] == 15  # untouched
    assert body["level"] == 1
    assert calls == []  # RPC must NOT be called when exp != 0

    persisted = _reload_usp(test_session, season.id)
    assert persisted.exp == 15
    assert persisted.level == 1


def test_status_worldclear_cleared_stage_zero_stays_zero(
    client, test_session, monkeypatch
):
    season, _ = _seed_worldclear(test_session, exp=0, level=0)

    monkeypatch.setattr(
        "app.api.user._fetch_cleared_stage", lambda planet_id, avatar_addr: 0
    )

    resp = client.get(
        "/api/user/status",
        params={
            "planet_id": ODIN,
            "agent_addr": AGENT,
            "avatar_addr": AVATAR,
            "pass_type": PassType.WORLD_CLEAR_PASS.value,
            "season_index": season.season_index,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exp"] == 0
    assert body["level"] == 0

    persisted = _reload_usp(test_session, season.id)
    assert persisted.exp == 0
    assert persisted.level == 0


def test_status_all_worldclear_backfills_exp_and_level(
    client, test_session, monkeypatch
):
    season, _ = _seed_worldclear(test_session, exp=0, level=0)

    calls = []

    def fake_fetch(planet_id, avatar_addr):
        calls.append((planet_id, avatar_addr))
        return 30  # maps to L3

    monkeypatch.setattr("app.api.user._fetch_cleared_stage", fake_fetch)

    resp = client.get(
        "/api/user/status/all",
        params={"planet_id": ODIN, "agent_addr": AGENT, "avatar_addr": AVATAR},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    wc = next(
        item for item in body if item["season_pass"]["pass_type"] == "WorldClearPass"
    )
    assert wc["exp"] == 30
    assert wc["level"] == 3
    assert len(calls) == 1

    persisted = _reload_usp(test_session, season.id)
    assert persisted.exp == 30
    assert persisted.level == 3
