from datetime import datetime, timedelta, timezone

import pytest
from conftest import add_test_data
from fastapi.testclient import TestClient

from common.enums import PassType, PlanetID
from common.models.season_pass import SeasonPass
from season_pass.main import app
from season_pass.schemas.season_pass import SeasonPassSchema

tc = TestClient(app)


@pytest.mark.usefixtures("sess")
@pytest.mark.parametrize(
    "planet_id",
    [PlanetID.ODIN_INTERNAL, PlanetID.HEIMDALL_INTERNAL, PlanetID.THOR_INTERNAL],
)
@pytest.mark.parametrize(
    "pass_type",
    [PassType.COURAGE_PASS, PassType.ADVENTURE_BOSS_PASS, PassType.ADVENTURE_BOSS_PASS],
)
def test_current_season(sess, planet_id, pass_type):
    season_data = SeasonPass(
        id=1,
        pass_type=PassType.COURAGE_PASS,
        season_index=1,
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
    )
    with add_test_data(sess, *[season_data]) as test_data:
        season_data = test_data[0]
        resp = tc.get(
            "/api/season-pass/current",
            params={
                "planet_id": planet_id.value.decode(),
                "pass_type": pass_type.COURAGE_PASS.value,
            },
        )
        assert resp.status_code == 200
        data = SeasonPassSchema(**resp.json())
        assert data.id == season_data.id
        assert data.start_timestamp == season_data.start_timestamp
        assert data.end_timestamp == season_data.end_timestamp
