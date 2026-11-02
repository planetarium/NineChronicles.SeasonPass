"""StakeAPCoef: StakeActionPointCoefficientSheet → stake deposit → AP coefficient (%)."""
from unittest.mock import MagicMock, patch

import pytest
from app.utils.stake import StakeAPCoef

SHEET = "level,required_gold,coefficient\n1,50,100\n2,500,100\n3,5000,80\n4,50000,80\n5,500000,80\n6,5000000,60\n7,10000000,60"


def state_hex(csv: str) -> str:
    text = f"{len(csv)}:{csv}"  # bencodex text, as the node returns the sheet state
    return text.encode().hex()


@pytest.fixture
def coef():
    c = StakeAPCoef(jwt_secret="test-secret", timeout=1)
    with patch("app.utils.stake.requests.post") as post:
        post.return_value = MagicMock(
            json=lambda: {"data": {"state": state_hex(SHEET)}}
        )
        c.set_url(gql_url="https://node.example/graphql")
    return c


@pytest.mark.parametrize(
    "deposit,expected",
    [
        (0, 100),
        (4999, 100),
        (5000, 80),
        (4_999_999, 80),
        (5_000_000, 60),
        (10**12, 60),
    ],
)
def test_coefficient_by_deposit(coef, deposit, expected):
    assert coef.get_ap_coef(deposit) == expected


def test_no_url_means_no_discount():
    c = StakeAPCoef()
    c.set_url(gql_url="")
    assert c.get_ap_coef(10**9) == 100
