import json
from datetime import timezone, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from common.enums import PlanetID
from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass
from conftest import TEST_AGENT_ADDR, TEST_AVATAR_ADDR, add_test_data
from season_pass.exceptions import NotPremiumError, InvalidSeasonError
from season_pass.main import app
from season_pass.schemas.user import UserSeasonPassSchema, ClaimResultSchema

tc = TestClient(app)


@pytest.mark.usefixtures("sess")
def test_prev_season_status_success(sess):
    now = datetime.now(tz=timezone.utc)
    prev_season_data = SeasonPass(
        id=1,
        start_timestamp=now - timedelta(days=3),
        end_timestamp=now - timedelta(days=1),
    )
    current_season_data = SeasonPass(
        id=2,
        start_timestamp=now - timedelta(days=1),
        end_timestamp=now + timedelta(days=1)
    )
    user_data = UserSeasonPass(
        planet_id=PlanetID.ODIN_INTERNAL, season_pass_id=1,
        agent_addr=TEST_AGENT_ADDR, avatar_addr=TEST_AVATAR_ADDR,
        is_premium=True
    )
    with add_test_data(sess, prev_season_data, current_season_data, user_data) as test_data:
        prev_season, current_season, test_user = test_data
        resp = tc.get("/api/user/status",
                      params={
                          "season_id": 1,
                          "avatar_addr": test_user.avatar_addr,
                          "planet_id": test_user.planet_id.decode(),
                      })
        assert resp.status_code == 200
        data = resp.json()
        data["planet_id"] = data["planet_id"].encode()
        result = UserSeasonPassSchema(**data)
        assert result.claim_limit_timestamp == prev_season.end_timestamp + timedelta(days=7)


@pytest.mark.parametrize(
    "season_delta, is_premium, level, exc",
    [(1, True, 1, None),
     (1, False, 1, NotPremiumError),  # Not Premium User
     (10, True, 1, InvalidSeasonError),  # Claim limit passed
     (-1, True, 1, InvalidSeasonError),  # Not prev. season
     ]
)
@pytest.mark.usefixtures("sess")
def test_prev_season_claim_success(sess, season_delta, is_premium, level, exc):
    now = datetime.now(tz=timezone.utc)
    prev_season_data = SeasonPass(
        id=1,
        start_timestamp=now - timedelta(days=season_delta - 2),
        end_timestamp=now - timedelta(days=season_delta),
    )
    current_season_data = SeasonPass(
        id=2,
        start_timestamp=now - timedelta(days=season_delta),
        end_timestamp=now + timedelta(days=1)
    )
    user_data = UserSeasonPass(
        planet_id=PlanetID.ODIN_INTERNAL, season_pass_id=1,
        agent_addr=TEST_AGENT_ADDR, avatar_addr=TEST_AVATAR_ADDR,
        is_premium=is_premium, level=level
    )
    with add_test_data(sess, prev_season_data, current_season_data, user_data) as test_data:
        prev_season, current_season, test_user = test_data

        if exc:
            with pytest.raises(exc):
                tc.post("/api/user/claim-prev",
                        content=json.dumps({
                            "planet_id": test_user.planet_id.decode(),
                            "agent_addr": test_user.agent_addr,
                            "avatar_addr": test_user.avatar_addr,
                            "season_id": 1,
                            "prev": True
                        }))
        else:
            resp = tc.post("/api/user/claim-prev",
                           content=json.dumps({
                               "planet_id": test_user.planet_id.decode(),
                               "agent_addr": test_user.agent_addr,
                               "avatar_addr": test_user.avatar_addr,
                               "season_id": 1,
                               "prev": True
                           }))
            assert resp.status_code == 200
            data = resp.json()
            data["user"]["planet_id"] = data["user"]["planet_id"].encode()
            result = ClaimResultSchema(**data)
            assert len(result.reward_list) > 0
