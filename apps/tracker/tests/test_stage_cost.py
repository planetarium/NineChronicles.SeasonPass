"""get_stage_cost_ap: StageSheet CostAP from the CDN, cached with ETag; default 5 when unknown."""
from unittest.mock import MagicMock, patch

import pytest
from app.utils import stage_cost
from shared.enums import PlanetID

CSV = "id,cost_ap,other\n1,5,x\n301,10,x\n502,30,x\nbad,1,x\n"


@pytest.fixture(autouse=True)
def clear_cache():
    stage_cost._stage_cost_cache.clear()
    stage_cost._etag_cache.clear()
    yield
    stage_cost._stage_cost_cache.clear()
    stage_cost._etag_cache.clear()


def resp(status=200, text=CSV, etag='"v1"'):
    r = MagicMock(status_code=status, text=text, headers={"ETag": etag} if etag else {})
    r.raise_for_status.side_effect = (
        None if status < 400 else Exception(f"HTTP {status}")
    )
    return r


def test_cost_from_sheet_and_default():
    with patch("app.utils.stage_cost.requests.get", return_value=resp()) as get:
        assert stage_cost.get_stage_cost_ap(PlanetID.HEIMDALL, 502) == 30
        assert stage_cost.get_stage_cost_ap(PlanetID.HEIMDALL, 301) == 10
        assert (
            stage_cost.get_stage_cost_ap(PlanetID.HEIMDALL, 9999)
            == stage_cost.DEFAULT_COST_AP
        )
    assert get.call_count == 1  # cached per planet
    assert get.call_args.args[0].endswith("/0x000000000001/StageSheet.csv")


def test_none_stage_is_default_without_fetch():
    with patch("app.utils.stage_cost.requests.get") as get:
        assert (
            stage_cost.get_stage_cost_ap(PlanetID.ODIN, None)
            == stage_cost.DEFAULT_COST_AP
        )
    get.assert_not_called()


def test_fetch_failure_falls_back_to_default():
    with patch("app.utils.stage_cost.requests.get", return_value=resp(status=503)):
        assert (
            stage_cost.get_stage_cost_ap(PlanetID.ODIN, 502)
            == stage_cost.DEFAULT_COST_AP
        )


def test_refresh_uses_etag_and_keeps_cache_on_304():
    with patch("app.utils.stage_cost.requests.get", return_value=resp()):
        stage_cost.get_stage_cost_ap(PlanetID.ODIN, 502)
    with patch(
        "app.utils.stage_cost.requests.get", return_value=resp(status=304, text="")
    ) as get:
        stage_cost.refresh_stage_sheets()
    assert get.call_args.kwargs["headers"] == {"If-None-Match": '"v1"'}
    assert stage_cost.get_stage_cost_ap(PlanetID.ODIN, 502) == 30


def test_refresh_replaces_changed_sheet():
    with patch("app.utils.stage_cost.requests.get", return_value=resp()):
        stage_cost.get_stage_cost_ap(PlanetID.ODIN, 502)
    with patch(
        "app.utils.stage_cost.requests.get",
        return_value=resp(text="id,cost_ap\n502,40\n", etag='"v2"'),
    ):
        stage_cost.refresh_stage_sheets()
    assert stage_cost.get_stage_cost_ap(PlanetID.ODIN, 502) == 40
