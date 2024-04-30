from datetime import datetime, timezone, timedelta

import pytest

from common.enums import PlanetID
from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass
from conftest import TEST_AGENT_ADDR, TEST_AVATAR_ADDR, add_test_data


@pytest.mark.usefixtures("sess")
def test_new_user(sess):
    now = datetime.now(tz=timezone.utc)
    season_data = SeasonPass(
        id=1, instant_exp=100,
        start_timestamp=now - timedelta(days=1),
        end_timestamp=now + timedelta(days=1),
    )
    user_data = UserSeasonPass(
        planet_id=PlanetID.ODIN_INTERNAL,
        agent_addr=TEST_AGENT_ADDR,
        avatar_addr=TEST_AVATAR_ADDR,
        season_pass_id=1
    )
    with add_test_data(sess, season_data, user_data) as test_data:
        test_season, test_user = test_data
        assert test_user.agent_addr == TEST_AGENT_ADDR
        assert test_user.avatar_addr == TEST_AVATAR_ADDR
        assert test_user.season_pass_id == 1
