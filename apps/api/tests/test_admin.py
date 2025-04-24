from datetime import datetime, timedelta, timezone

import pytest
from shared.enums import TxStatus

from .conftest import TEST_AVATAR_ADDR


@pytest.mark.parametrize("status", [None, TxStatus.SUCCESS, TxStatus.FAILURE])
def test_get_claims_with_status(client, valid_token, test_claims, status):
    """상태별 Claim 조회"""
    params = {"status": status.value if status else None}
    response = client.get(
        "/api/admin/claims",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={k: v for k, v in params.items() if v is not None},
    )
    assert response.status_code == 200
    claims = response.json()

    if status:
        assert all(claim["tx_status"] == status.value for claim in claims)
    else:
        assert len(claims) == 2


def test_get_claims_with_avatar(client, valid_token, test_claims):
    """특정 사용자의 Claim 조회"""
    response = client.get(
        "/api/admin/claims",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"avatar_addr": TEST_AVATAR_ADDR},
    )
    assert response.status_code == 200
    claims = response.json()
    assert all(claim["avatar_addr"] == TEST_AVATAR_ADDR for claim in claims)


def test_get_claims_with_days(client, valid_token, test_claims):
    """기간별 Claim 조회"""
    response = client.get(
        "/api/admin/claims",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"days": 1},
    )
    assert response.status_code == 200
    claims = response.json()
    now = datetime.now(tz=timezone.utc)
    assert all(
        datetime.fromisoformat(claim["created_at"]) >= now - timedelta(days=1)
        for claim in claims
    )


def test_get_claims_unauthorized(client):
    """인증 없이 Claim 조회 시도"""
    response = client.get("/api/admin/claims")
    assert response.status_code == 422
