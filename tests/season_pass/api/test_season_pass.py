from datetime import timezone, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from common.models.season_pass import SeasonPass
from conftest import add_test_data
from season_pass.main import app
from season_pass.schemas.season_pass import SeasonPassSchema

tc = TestClient(app)


@pytest.mark.usefixtures("sess")
def test_current_season(sess):
    season_data = SeasonPass(
        id=1,
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
    )
    with add_test_data(sess, *[season_data]) as test_data:
        season_data = test_data[0]
        resp = tc.get("/api/season-pass/current")
        assert resp.status_code == 200
        data = SeasonPassSchema(**resp.json())
        assert data.id == season_data.id
        assert data.start_timestamp == season_data.start_timestamp
        assert data.end_timestamp == season_data.end_timestamp
