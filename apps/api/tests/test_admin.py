from datetime import datetime, timedelta, timezone

import jwt
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
    data = response.json()

    if status:
        assert all(claim["tx_status"] == status.value for claim in data["items"])
    else:
        assert len(data["items"]) == 2


def test_get_claims_with_avatar(client, valid_token, test_claims):
    """특정 사용자의 Claim 조회"""
    response = client.get(
        "/api/admin/claims",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"avatar_addr": TEST_AVATAR_ADDR},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(claim["avatar_addr"] == TEST_AVATAR_ADDR for claim in data["items"])


def test_get_claims_with_days(client, valid_token, test_claims):
    """기간별 Claim 조회"""
    response = client.get(
        "/api/admin/claims",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"days": 1},
    )
    assert response.status_code == 200
    data = response.json()
    now = datetime.now(tz=timezone.utc)
    assert all(
        datetime.fromisoformat(claim["created_at"]) >= now - timedelta(days=1)
        for claim in data["items"]
    )


def test_get_claims_unauthorized(client, app_config):
    """인증 실패 테스트"""
    # 인증 헤더 없음
    resp = client.get("/api/admin/claims")
    assert resp.status_code == 403

    # 잘못된 토큰
    resp = client.get(
        "/api/admin/claims", headers={"Authorization": "Bearer invalid_token"}
    )
    assert resp.status_code == 401

    # 만료된 토큰
    now = datetime.now(tz=timezone.utc)
    expired_payload = {
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
        "aud": "SeasonPass",
    }
    expired_token = jwt.encode(
        expired_payload, app_config.jwt_secret, algorithm="HS256"
    )
    resp = client.get(
        "/api/admin/claims", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert resp.status_code == 401


def test_get_claims_invalid_params(client, valid_token):
    """잘못된 파라미터 테스트"""
    # 잘못된 limit 값 테스트
    resp = client.get(
        "/api/admin/claims?limit=0", headers={"Authorization": f"Bearer {valid_token}"}
    )
    assert resp.status_code == 422

    resp = client.get(
        "/api/admin/claims?limit=101",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp.status_code == 422

    # 잘못된 offset 값 테스트
    resp = client.get(
        "/api/admin/claims?offset=-1",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp.status_code == 422

    # 잘못된 days 값 테스트
    resp = client.get(
        "/api/admin/claims?days=0", headers={"Authorization": f"Bearer {valid_token}"}
    )
    assert resp.status_code == 422

    resp = client.get(
        "/api/admin/claims?days=31", headers={"Authorization": f"Bearer {valid_token}"}
    )
    assert resp.status_code == 422
