"""/api/block-status — the tracker-lag monitor (Assertible "Test Block Status").

It used to read each planet's tip from ONE node with a 2s timeout and treat a
failed read as tip 0, so abs(0 - processed) > 150 paged whenever that node
restarted (the odin validator crashes several times a day on a NetMQ bug).
Now it asks several nodes in parallel, takes the highest tip, pages only when a
tracker TRAILS the tip by more than the threshold, and reports "tip
unavailable" (still 503) only when no node answers at all.
"""
import pytest
import requests
from shared.enums import PassType, PlanetID
from shared.models.action import Block

import app.api as api
from app.config import config

ODIN_PRIMARY = "https://odin-validator.example/graphql"
ODIN_FALLBACK = "https://odin-rpc.example/graphql"
HEIMDALL_PRIMARY = "https://heimdall-gql.example/graphql"


class _Resp:
    def __init__(self, status, tip=None):
        self.status_code = status
        self._tip = tip

    def json(self):
        return {"data": {"nodeStatus": {"tip": {"index": self._tip}}}}


@pytest.fixture
def nodes(monkeypatch):
    """url -> tip (int), HTTP status (str "500"), or an exception to raise."""
    answers = {}

    def fake_post(url, **kwargs):
        a = answers[url]
        if isinstance(a, Exception):
            raise a
        if isinstance(a, str):
            return _Resp(int(a))
        return _Resp(200, a)

    monkeypatch.setattr(api.requests, "post", fake_post)
    monkeypatch.setattr(config, "stage", "mainnet")
    monkeypatch.setattr(config, "headless_jwt_secret", "test-headless-secret")
    monkeypatch.setattr(
        config,
        "gql_url_map",
        {"0x000000000000": ODIN_PRIMARY, "0x000000000001": HEIMDALL_PRIMARY},
    )
    monkeypatch.setattr(
        config,
        "tip_fallback_url_map",
        {"0x000000000000": [ODIN_FALLBACK, ODIN_PRIMARY]},
    )
    monkeypatch.setattr(config, "enabled_planets", ["0x000000000000", "0x000000000001"])
    return answers


def _blocks(test_session, planet, processed):
    for pass_type in (PassType.COURAGE_PASS, PassType.WORLD_CLEAR_PASS):
        test_session.add(
            Block(planet_id=planet, pass_type=pass_type, last_processed_index=processed)
        )
    test_session.commit()


def test_healthy(client, test_session, nodes):
    _blocks(test_session, PlanetID.ODIN, 1000)
    _blocks(test_session, PlanetID.HEIMDALL, 500)
    nodes.update({ODIN_PRIMARY: 1002, ODIN_FALLBACK: 1001, HEIMDALL_PRIMARY: 501})
    resp = client.get("/api/block-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["ODIN"].values()) == {2}  # highest tip wins
    assert set(body["HEIMDALL"].values()) == {1}


@pytest.mark.parametrize(
    "down",
    [
        requests.exceptions.ReadTimeout("slow"),
        "502",
        requests.exceptions.ConnectionError("x"),
    ],
)
def test_primary_down_uses_fallback(client, test_session, nodes, down):
    """The 15:15 pages (10/31, 11/2): odin validator restarting."""
    _blocks(test_session, PlanetID.ODIN, 1000)
    _blocks(test_session, PlanetID.HEIMDALL, 500)
    nodes.update({ODIN_PRIMARY: down, ODIN_FALLBACK: 1003, HEIMDALL_PRIMARY: 500})
    resp = client.get("/api/block-status")
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["ODIN"].values()) == {3}


def test_tracker_lagging_pages(client, test_session, nodes):
    _blocks(test_session, PlanetID.ODIN, 1000)
    _blocks(test_session, PlanetID.HEIMDALL, 500)
    nodes.update(
        {
            ODIN_PRIMARY: 1000 + config.block_lag_threshold + 1,
            ODIN_FALLBACK: 1000,
            HEIMDALL_PRIMARY: 500,
        }
    )
    assert client.get("/api/block-status").status_code == 503


def test_tracker_ahead_of_tip_does_not_page(client, test_session, nodes):
    """A node behind the tracker used to page through abs()."""
    _blocks(test_session, PlanetID.ODIN, 1000)
    _blocks(test_session, PlanetID.HEIMDALL, 500)
    nodes.update({ODIN_PRIMARY: 700, ODIN_FALLBACK: "500", HEIMDALL_PRIMARY: 500})
    resp = client.get("/api/block-status")
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["ODIN"].values()) == {-300}


def test_all_nodes_down_is_reported_as_such(client, test_session, nodes):
    _blocks(test_session, PlanetID.ODIN, 1000)
    _blocks(test_session, PlanetID.HEIMDALL, 500)
    nodes.update(
        {
            ODIN_PRIMARY: "503",
            ODIN_FALLBACK: requests.exceptions.ReadTimeout("x"),
            HEIMDALL_PRIMARY: 500,
        }
    )
    resp = client.get("/api/block-status")
    assert resp.status_code == 503
    assert resp.json()["ODIN"] == {"error": "tip unavailable"}
    assert set(resp.json()["HEIMDALL"].values()) == {0}


def test_tip_urls_dedup_primary_first(nodes):
    assert api.tip_urls(PlanetID.ODIN) == [ODIN_PRIMARY, ODIN_FALLBACK]
    assert api.tip_urls(PlanetID.HEIMDALL) == [HEIMDALL_PRIMARY]
