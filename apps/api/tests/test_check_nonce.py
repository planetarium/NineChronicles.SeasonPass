"""/api/check-nonce logs the planet and both nonces with a real format string.

It used to call logging.info(highest_nonce, next_nonce), which uses the int as the
format string: every emitted record failed with a "Logging error" traceback.
"""
import logging

import pytest
from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim

import app.api as api
from app.config import config

ODIN_URL = "https://odin.example/graphql"


class _Resp:
    def json(self):
        return {"data": {"nextTxNonce": 42}}


@pytest.fixture
def node(monkeypatch):
    monkeypatch.setattr(api.requests, "post", lambda url, **kw: _Resp())
    monkeypatch.setattr(config, "stage", "mainnet")
    monkeypatch.setattr(config, "headless_jwt_secret", "test-headless-secret")
    monkeypatch.setattr(config, "gql_url_map", {"0x000000000000": ODIN_URL})


def test_check_nonce_log_line(client, test_session, node, caplog):
    test_session.add(
        Claim(
            uuid="nonce-40",
            agent_addr="0xagent",
            avatar_addr="0xavatar",
            planet_id=PlanetID.ODIN,
            reward_list=[],
            nonce=40,
            tx_status=TxStatus.STAGED,
        )
    )
    test_session.commit()
    caplog.set_level(logging.INFO)

    resp = client.get("/api/check-nonce", params={"planet": "odin"})

    assert resp.status_code == 200, resp.text
    assert (
        "check-nonce planet_id=0x000000000000 highest_nonce=40 next_nonce=42"
        in caplog.messages
    )
