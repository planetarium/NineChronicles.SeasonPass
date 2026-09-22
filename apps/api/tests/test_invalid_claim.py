"""Regression tests for /api/invalid-claim after it was switched from
"503 if any" to returning the COUNT of stuck claims (mirrors IAP
/purchase/invalid-receipt-count). The alert threshold now lives in the monitor;
the endpoint just reports the number so a single transient slow tx doesn't page.

A claim counts as stuck when it is older than 5 minutes, has rewards, and has
not reached SUCCESS (STAGED / INVALID / FAILURE / NULL).
"""
from datetime import datetime, timedelta, timezone

from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim

AGENT = "0xc1a10000000000000000000000000000000000a1"
AVATAR = "0xc1a10000000000000000000000000000000000a2"
REWARD = [{"ticker": "CRYSTAL", "amount": 1, "decimal_places": 0}]


def _claim(test_session, uuid, *, age_min, tx_status, reward=REWARD):
    test_session.add(
        Claim(
            uuid=uuid,
            agent_addr=AGENT,
            avatar_addr=AVATAR,
            planet_id=PlanetID.ODIN,
            reward_list=reward,
            tx_status=tx_status,
            created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=age_min),
        )
    )


def test_invalid_claim_returns_zero_when_none_stuck(client, test_session):
    _claim(test_session, "ok1", age_min=10, tx_status=TxStatus.SUCCESS)
    _claim(test_session, "ok2", age_min=1, tx_status=TxStatus.STAGED)  # too recent
    test_session.commit()

    resp = client.get("/api/invalid-claim")
    assert resp.status_code == 200, resp.text
    assert resp.json() == 0


def test_invalid_claim_returns_count_of_stuck(client, test_session):
    # counted: >5min old, has rewards, not SUCCESS
    _claim(test_session, "staged_old", age_min=10, tx_status=TxStatus.STAGED)
    _claim(test_session, "invalid_old", age_min=10, tx_status=TxStatus.INVALID)
    _claim(test_session, "null_old", age_min=10, tx_status=None)
    # NOT counted:
    _claim(test_session, "success_old", age_min=10, tx_status=TxStatus.SUCCESS)
    _claim(test_session, "staged_recent", age_min=1, tx_status=TxStatus.STAGED)
    _claim(
        test_session, "noreward_old", age_min=10, tx_status=TxStatus.STAGED, reward=[]
    )
    test_session.commit()

    resp = client.get("/api/invalid-claim")
    assert resp.status_code == 200, resp.text
    assert resp.json() == 3  # staged_old + invalid_old + null_old
