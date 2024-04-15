import pytest

from common.models.user import UserSeasonPass
from tests.conftest import TEST_AGENT_ADDR, TEST_AVATAR_ADDR


@pytest.mark.usefixtures("new_user")
def test_new_user(new_user: UserSeasonPass):
    assert new_user.agent_addr == TEST_AGENT_ADDR
    assert new_user.avatar_addr == TEST_AVATAR_ADDR
    assert new_user.season_pass_id == 1
