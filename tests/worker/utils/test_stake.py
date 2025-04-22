import os

import pytest
from worker.utils.stake import StakeAPCoef

from season_pass import settings


@pytest.mark.parametrize(
    ("stake", "expected"),
    [
        (0, 100),
        (100, 100),
        (500, 100),
        (1000, 100),
        (5000, 80),
        (10000, 80),
        (100_000, 80),
        (500_000, 60),
        (1_000_000, 60),
        (5_000_000, 60),
        (10_000_000, 60),
    ],
)
def test_sweep_coef(stake, expected):
    coef = StakeAPCoef(jwt_secret=settings.HEADLESS_GQL_JWT_SECRET)
    coef.set_url(gql_url=os.environ.get("ODIN_GQL_URL"))
    assert coef.get_ap_coef(stake) == expected
