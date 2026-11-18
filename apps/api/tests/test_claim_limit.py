"""/api/user/claim refuses new claims (ServerOverloadError, 503) once more than 50 claims with rewards
are STAGED or INVALID. The count is per planet: on 2026-11-16 Odin's chain halt
left 51 INVALID Odin claims and the global count refused Heimdall claims too.
"""
from datetime import datetime, timedelta, timezone

import pytest
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.season_pass import Level, SeasonPass
from shared.models.user import Claim, UserSeasonPass

from app.api import user as user_api
from app.exceptions import ServerOverloadError

AGENT = "0xc1a20000000000000000000000000000000000a1"
AVATAR = "0xc1a20000000000000000000000000000000000a2"
REWARD = [{"ticker": "CRYSTAL", "amount": 1, "decimal_places": 0}]


@pytest.fixture
def claimable(test_session, monkeypatch):
    """A current Courage pass with level 1 claimable for AVATAR on Odin and Heimdall."""
    monkeypatch.setattr(user_api, "send_to_worker", lambda *a, **kw: "task")
    now = datetime.now(tz=timezone.utc)
    test_session.add(
        SeasonPass(
            id=1,
            pass_type=PassType.COURAGE_PASS,
            season_index=1,
            start_timestamp=now - timedelta(days=1),
            end_timestamp=now + timedelta(days=1),
            instant_exp=0,
            reward_list=[{"level": 1, "normal": REWARD, "premium": []}],
        )
    )
    for level, exp in ((1, 0), (2, 100), (3, 200)):
        test_session.add(Level(pass_type=PassType.COURAGE_PASS, level=level, exp=exp))
    for planet in (PlanetID.ODIN, PlanetID.HEIMDALL):
        test_session.add(
            UserSeasonPass(
                planet_id=planet,
                agent_addr=AGENT,
                avatar_addr=AVATAR,
                season_pass_id=1,
                level=1,
                exp=0,
            )
        )
    test_session.commit()


def _stuck(test_session, planet, count, tx_status=TxStatus.INVALID):
    for i in range(count):
        test_session.add(
            Claim(
                uuid=f"stuck-{planet.name}-{tx_status.name}-{i}",
                agent_addr=AGENT,
                avatar_addr=AVATAR,
                planet_id=planet,
                reward_list=REWARD,
                tx_status=tx_status,
            )
        )
    test_session.commit()


def _claim(client, planet):
    return client.post(
        "/api/user/claim",
        json={
            "planet_id": planet.value.decode(),
            "agent_addr": AGENT,
            "avatar_addr": AVATAR,
            "pass_type": PassType.COURAGE_PASS.value,
            "season_index": 1,
        },
    )


def test_other_planet_stuck_claims_do_not_block(client, test_session, claimable):
    _stuck(test_session, PlanetID.ODIN, 51)

    resp = _claim(client, PlanetID.HEIMDALL)
    assert resp.status_code == 200, resp.text
    assert [r["ticker"] for r in resp.json()["reward_list"]] == ["CRYSTAL"]


def test_same_planet_over_limit_is_refused(client, test_session, claimable):
    _stuck(test_session, PlanetID.HEIMDALL, 26)
    _stuck(test_session, PlanetID.HEIMDALL, 25, TxStatus.STAGED)

    # main.py maps this to 503; TestClient re-raises instead of calling the handler.
    with pytest.raises(ServerOverloadError):
        _claim(client, PlanetID.HEIMDALL)


def test_same_planet_at_limit_is_allowed(client, test_session, claimable):
    _stuck(test_session, PlanetID.HEIMDALL, 50)

    resp = _claim(client, PlanetID.HEIMDALL)
    assert resp.status_code == 200, resp.text


def test_inprogress_claim_count_per_planet(test_session):
    _stuck(test_session, PlanetID.ODIN, 3)
    _stuck(test_session, PlanetID.ODIN, 2, TxStatus.STAGED)
    _stuck(test_session, PlanetID.ODIN, 4, TxStatus.SUCCESS)
    _stuck(test_session, PlanetID.HEIMDALL, 1)
    test_session.add(
        Claim(
            uuid="no-reward",
            agent_addr=AGENT,
            avatar_addr=AVATAR,
            planet_id=PlanetID.ODIN,
            reward_list=[],
            tx_status=TxStatus.INVALID,
        )
    )
    test_session.commit()

    assert user_api.inprogress_claim_count(test_session, PlanetID.ODIN) == 5
    assert user_api.inprogress_claim_count(test_session, PlanetID.HEIMDALL) == 1
    assert user_api.inprogress_claim_count(test_session, PlanetID.THOR) == 0
