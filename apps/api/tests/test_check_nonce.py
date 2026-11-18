"""/api/check-nonce — the only monitor (Assertible, every 5 min) pages on its status code.

gap = DB highest claim nonce − node nextTxNonce + 1. Normal is 0. It used to 503 only when
highest > next + 100, which the 50-claims-per-planet limit makes unreachable (2026-11-16: gap
about 50, no alert); node errors and "no claims" ended in 500/204, and the INFO log line is
dropped in production (root logger stays at WARNING). caplog is left at that level on purpose.
"""
import logging

import pytest
import requests
from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim
from sqlalchemy import update

import app.api as api
from app.config import config

ODIN_URL = "https://odin.example/graphql"
ODIN_NONCE_URL = "https://odin-validator.example/graphql"
HEIMDALL_URL = "https://heimdall.example/graphql"
MIN = 60.0


class _Resp:
    def __init__(self, body=None, status_code=200, json_exc=None):
        self.status_code = status_code
        self._body = body
        self._json_exc = json_exc

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._body


class Node:
    """Fake headless: answers nextTxNonce, or whatever a test puts in `reply`."""

    def __init__(self):
        self.next_nonce = 42
        self.reply = None
        self.urls = []

    def post(self, url, **kw):
        self.urls.append(url)
        if self.reply is not None:
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        return _Resp({"data": {"nextTxNonce": self.next_nonce}})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def node(monkeypatch):
    fake = Node()
    monkeypatch.setattr(api.requests, "post", fake.post)
    monkeypatch.setattr(config, "stage", "mainnet")
    monkeypatch.setattr(config, "headless_jwt_secret", "test-headless-secret")
    monkeypatch.setattr(
        config,
        "gql_url_map",
        {"0x000000000000": ODIN_URL, "0x000000000001": HEIMDALL_URL},
    )
    # check_nonce_* and nonce_gql_url_map stay at the app defaults (25 min, 60, 10, {}).
    return fake


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(api, "_now", c, raising=False)
    return c


@pytest.fixture(autouse=True)
def reset_hole_state():
    getattr(api, "_hole_seen", {}).clear()
    yield
    getattr(api, "_hole_seen", {}).clear()


def set_highest(sess, highest, planet_id=PlanetID.ODIN, tag=""):
    """A claim holding `highest`: the DB's highest nonce for the planet (unless one is higher)."""
    sess.add(
        Claim(
            uuid=f"{planet_id.value.decode()}-{highest}{tag}",
            agent_addr="0xagent",
            avatar_addr="0xavatar",
            planet_id=planet_id,
            reward_list=[],
            nonce=highest,
            tx_status=TxStatus.STAGED,
        )
    )
    sess.commit()


def get(client, planet="odin"):
    return client.get("/api/check-nonce", params={"planet": planet})


def warnings_(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "check-nonce" in r.getMessage()
    ]


def test_ok_keeps_node_body_and_adds_check(client, test_session, node, caplog):
    set_highest(test_session, 41)

    resp = get(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == {"nextTxNonce": 42}  # unchanged for whoever reads it
    check = body["check"]
    assert check["planet_id"] == "0x000000000000"
    assert (check["highest_nonce"], check["next_nonce"], check["gap"]) == (41, 42, 0)
    assert check["reason"] == "ok"
    assert check["max_gap"] == 60 and check["stuck_minutes"] == 25.0
    assert warnings_(caplog) == []


def test_ok_info_log_line(client, test_session, node, caplog):
    set_highest(test_session, 41)
    caplog.set_level(logging.INFO)

    assert get(client).status_code == 200

    assert (
        "check-nonce planet_id=0x000000000000 highest_nonce=41 next_nonce=42 gap=0 "
        "stuck_seconds=0 reason=ok" in caplog.messages
    )


def test_no_claims_is_ok(client, test_session, node, caplog):
    resp = get(client)

    assert resp.status_code == 200, resp.text
    check = resp.json()["check"]
    assert check["reason"] == "no_claims"
    assert check["highest_nonce"] is None and check["gap"] is None
    assert warnings_(caplog) == []


@pytest.mark.parametrize(
    "reply,error",
    [
        (_Resp(json_exc=ValueError("Expecting value")), "ValueError"),
        (_Resp({"errors": [{"message": "boom"}]}), "GraphQLError"),
        (_Resp({"data": {"nextTxNonce": None}}), "GraphQLError"),
        (requests.exceptions.ReadTimeout("read timed out"), "ReadTimeout"),
        (
            requests.exceptions.ConnectionError(
                f"HTTPSConnectionPool(host='{ODIN_URL}'): refused"
            ),
            "ConnectionError",
        ),
        (_Resp({"data": {"nextTxNonce": 42}}, status_code=502), "HTTPError"),
    ],
    ids=["not-json", "gql-errors", "null-nonce", "timeout", "conn-error", "http-502"],
)
def test_node_error_is_503_unavailable(
    client, test_session, node, caplog, reply, error
):
    set_highest(test_session, 41)
    node.reply = reply

    resp = get(client)

    assert resp.status_code == 503, resp.text
    check = resp.json()["check"]
    assert check["reason"] == "nonce_unavailable"
    assert check["error"] == error
    assert ODIN_URL not in resp.text  # node URLs come from a secret
    assert any("reason=nonce_unavailable" in m for m in warnings_(caplog))


def test_planet_without_url_is_503_unavailable(client, test_session, node, monkeypatch):
    monkeypatch.setattr(config, "gql_url_map", {"0x000000000001": HEIMDALL_URL})

    resp = get(client)

    assert resp.status_code == 503, resp.text
    assert resp.json()["check"]["reason"] == "nonce_unavailable"
    assert node.urls == []


def test_gap_over_max_is_503_at_once(client, test_session, node, clock):
    set_highest(test_session, 41 + 61)  # gap 61

    resp = get(client)

    assert resp.status_code == 503, resp.text
    check = resp.json()["check"]
    assert (check["gap"], check["reason"]) == (61, "gap_too_large")
    assert resp.json()["data"] == {"nextTxNonce": 42}


def test_gap_at_max_is_pending(client, test_session, node, clock, caplog):
    set_highest(test_session, 41 + 60)  # gap 60: the 50-claim limit plus slack

    resp = get(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["check"]["reason"] == "pending"
    assert any("reason=pending" in m for m in warnings_(caplog))


def test_hole_without_progress_turns_503_after_stuck_minutes(
    client, test_session, node, clock, caplog
):
    set_highest(test_session, 44)  # next 42 → gap 3

    assert get(client).json()["check"]["stuck_seconds"] == 0
    clock.t += 25 * MIN - 1
    resp = get(client)
    assert resp.status_code == 200 and resp.json()["check"]["reason"] == "pending"

    clock.t += 1
    resp = get(client)

    assert resp.status_code == 503, resp.text
    check = resp.json()["check"]
    assert (check["gap"], check["reason"], check["stuck_seconds"]) == (
        3,
        "hole_stuck",
        1500,
    )
    assert any(
        "reason=hole_stuck" in m and "stuck_seconds=1500" in m
        for m in warnings_(caplog)
    )


def test_progress_resets_stuck_time(client, test_session, node, clock):
    # Re-signing (B2) fills 20 nonces per round: nextTxNonce moves, so a hole that is being
    # filled must not page even if the gap stays above zero for longer than stuck_minutes.
    set_highest(test_session, 44)
    get(client)
    clock.t += 25 * MIN
    assert get(client).status_code == 503

    node.next_nonce = 43  # gap 2, but the node moved
    resp = get(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["check"]["stuck_seconds"] == 0

    clock.t += 25 * MIN - 1
    assert get(client).status_code == 200
    clock.t += 1
    assert get(client).status_code == 503


def test_gap_zero_clears_state(client, test_session, node, clock):
    set_highest(test_session, 41)
    set_highest(test_session, 44)  # gap 3 at next 42
    get(client)

    clock.t += 20 * MIN
    test_session.execute(update(Claim).where(Claim.nonce == 44).values(nonce=None))
    test_session.commit()
    assert get(client).json()["check"]["reason"] == "ok"  # gap 0 at the same next

    clock.t += 1 * MIN
    set_highest(test_session, 44, tag="-again")
    resp = get(client)  # gap 3 again at the same next: counted from now, not from t0
    assert resp.json()["check"]["stuck_seconds"] == 0

    clock.t += 25 * MIN - 1
    assert get(client).status_code == 200
    clock.t += 1
    assert get(client).status_code == 503


def test_chain_ahead(client, test_session, node, clock, caplog):
    # The chain used nonces the DB never handed out (burns), within the allowance.
    set_highest(test_session, 31)  # gap −10
    resp = get(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["check"]["reason"] == "chain_ahead"
    assert any("reason=chain_ahead" in m for m in warnings_(caplog))

    node.next_nonce = 43  # gap −11
    resp = get(client)
    assert resp.status_code == 503, resp.text
    assert resp.json()["check"]["reason"] == "chain_ahead_too_far"


def test_nonce_url_map_overrides_gql_url_map(client, test_session, node, monkeypatch):
    set_highest(test_session, 41)
    monkeypatch.setattr(config, "nonce_gql_url_map", {"0x000000000000": ODIN_NONCE_URL})

    assert get(client).status_code == 200
    assert get(client, "heimdall").status_code == 200

    assert node.urls == [ODIN_NONCE_URL, HEIMDALL_URL]


def test_planets_are_tracked_separately(client, test_session, node, clock):
    set_highest(test_session, 44)  # odin gap 3
    set_highest(test_session, 41, planet_id=PlanetID.HEIMDALL)  # heimdall gap 0
    get(client)
    clock.t += 25 * MIN
    assert get(client, "heimdall").status_code == 200
    assert get(client).status_code == 503


def test_invalid_planet_is_400(client, test_session, node):
    resp = get(client, "foo")
    assert resp.status_code == 400
    assert node.urls == []
