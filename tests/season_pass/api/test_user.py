from datetime import timezone, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from common.enums import PlanetID
from common.models.season_pass import SeasonPass
from common.models.user import UserSeasonPass
from conftest import TEST_AGENT_ADDR, TEST_AVATAR_ADDR, add_test_data
from season_pass.main import app
from season_pass.schemas.user import UserSeasonPassSchema

tc = TestClient(app)


