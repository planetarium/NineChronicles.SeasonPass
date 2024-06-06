import pytest

from common.utils._graphql import GQL
from common.enums import PlanetID
from season_pass import settings


@pytest.mark.parametrize("planet_id", [
    PlanetID.ODIN_INTERNAL,
    PlanetID.HEIMDALL_INTERNAL,
])
def test_gql_jwt(planet_id):
    gql = GQL(settings.HEADLESS_GQL_JWT_SECRET)
    test_nonce = gql.get_next_nonce(planet_id, "0x0000000000000000000000000000000000000000")
    assert test_nonce == 0
