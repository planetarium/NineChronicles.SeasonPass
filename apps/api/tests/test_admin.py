from datetime import datetime, timedelta, timezone

import jwt
import pytest
from shared.enums import ActionType, PassType, TxStatus

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


# 시즌패스 관리 API 테스트
def test_get_season_passes(client, valid_token, test_session):
    """시즌패스 목록 조회 테스트"""
    # 먼저 테스트 데이터 생성
    season_pass_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [
            {"action_type": ActionType.HAS.value, "exp": 100},
            {"action_type": ActionType.SWEEP.value, "exp": 50},
        ],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200

    # 시즌패스 목록 조회
    response = client.get(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    # 생성된 시즌패스가 목록에 있는지 확인
    created_season_pass = None
    for season_pass in data:
        if (
            season_pass["pass_type"] == PassType.COURAGE_PASS.value
            and season_pass["season_index"] == 1
        ):
            created_season_pass = season_pass
            break

    assert created_season_pass is not None
    assert created_season_pass["instant_exp"] == 1000
    assert len(created_season_pass["exp_list"]) == 2


def test_get_season_passes_with_filter(client, valid_token, test_session):
    """필터링된 시즌패스 목록 조회 테스트"""
    # 먼저 다른 타입의 시즌패스도 생성
    season_pass_data1 = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [],
    }

    season_pass_data2 = {
        "pass_type": PassType.WORLD_CLEAR_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 2000,
        "exp_list": [],
    }

    # 두 개의 시즌패스 생성
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data1,
    )
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data2,
    )

    # COURAGE_PASS 타입만 필터링하여 조회
    response = client.get(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"pass_type": PassType.COURAGE_PASS.value},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    # 모든 결과가 COURAGE_PASS 타입인지 확인
    for season_pass in data:
        assert season_pass["pass_type"] == PassType.COURAGE_PASS.value


def test_get_season_passes_with_season_index_filter(client, valid_token, test_session):
    """시즌 인덱스로 필터링된 시즌패스 목록 조회 테스트"""
    # 먼저 다른 시즌 인덱스의 시즌패스도 생성
    season_pass_data1 = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [],
    }

    season_pass_data2 = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 2,
        "reward_list": [],
        "instant_exp": 2000,
        "exp_list": [],
    }

    # 두 개의 시즌패스 생성
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data1,
    )
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data2,
    )

    # 시즌 인덱스 1만 필터링하여 조회
    response = client.get(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"season_index": 1},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    # 모든 결과가 시즌 인덱스 1인지 확인
    for season_pass in data:
        assert season_pass["season_index"] == 1


def test_get_season_passes_with_both_filters(client, valid_token, test_session):
    """패스 타입과 시즌 인덱스 모두로 필터링된 시즌패스 목록 조회 테스트"""
    # 먼저 다양한 조합의 시즌패스 생성
    season_pass_data1 = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [],
    }

    season_pass_data2 = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 2,
        "reward_list": [],
        "instant_exp": 2000,
        "exp_list": [],
    }

    season_pass_data3 = {
        "pass_type": PassType.WORLD_CLEAR_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 3000,
        "exp_list": [],
    }

    # 세 개의 시즌패스 생성
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data1,
    )
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data2,
    )
    client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data3,
    )

    # COURAGE_PASS 타입이고 시즌 인덱스가 1인 것만 필터링하여 조회
    response = client.get(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        params={"pass_type": PassType.COURAGE_PASS.value, "season_index": 1},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    # 모든 결과가 COURAGE_PASS 타입이고 시즌 인덱스가 1인지 확인
    for season_pass in data:
        assert season_pass["pass_type"] == PassType.COURAGE_PASS.value
        assert season_pass["season_index"] == 1


def test_create_season_pass_success(client, valid_token, test_session):
    """시즌패스 생성 성공 테스트"""
    season_pass_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "start_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "end_timestamp": (
            datetime.now(tz=timezone.utc) + timedelta(days=30)
        ).isoformat(),
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [
            {"action_type": ActionType.HAS.value, "exp": 100},
            {"action_type": ActionType.SWEEP.value, "exp": 50},
        ],
    }

    response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["pass_type"] == PassType.COURAGE_PASS.value
    assert data["season_index"] == 1
    assert data["instant_exp"] == 1000
    assert len(data["exp_list"]) == 2


def test_create_season_pass_duplicate(client, valid_token, test_session):
    """중복 시즌패스 생성 실패 테스트"""
    # 첫 번째 시즌패스 생성
    season_pass_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 1000,
        "exp_list": [],
    }

    response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert response.status_code == 200

    # 동일한 패스 타입과 시즌 인덱스로 다시 생성 시도
    response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_get_season_pass_success(client, valid_token, test_session):
    """특정 시즌패스 조회 성공 테스트"""
    # 먼저 시즌패스 생성
    season_pass_data = {
        "pass_type": PassType.ADVENTURE_BOSS_PASS.value,
        "season_index": 2,
        "reward_list": [],
        "instant_exp": 500,
        "exp_list": [],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200
    created_data = create_response.json()

    # 생성된 시즌패스 조회
    response = client.get(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["id"] == created_data["id"]
    assert data["pass_type"] == PassType.ADVENTURE_BOSS_PASS.value
    assert data["season_index"] == 2

    # 새로운 스키마 필드들 확인
    assert "start_date" in data
    assert "end_date" in data
    assert "repeat_last_reward" in data
    # Adventure Boss Pass는 repeat_last_reward가 True여야 함
    assert data["repeat_last_reward"] == True


def test_world_clear_pass_repeat_last_reward(client, valid_token, test_session):
    """World Clear Pass의 repeat_last_reward 동작 확인"""
    # World Clear Pass 생성
    season_pass_data = {
        "pass_type": PassType.WORLD_CLEAR_PASS.value,
        "season_index": 4,
        "reward_list": [],
        "instant_exp": 100,
        "exp_list": [],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200
    created_data = create_response.json()

    # 생성된 시즌패스 조회
    response = client.get(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    # World Clear Pass는 repeat_last_reward가 False여야 함
    assert data["repeat_last_reward"] == False


def test_courage_pass_repeat_last_reward(client, valid_token, test_session):
    """Courage Pass의 repeat_last_reward 동작 확인"""
    # Courage Pass 생성
    season_pass_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 5,
        "reward_list": [],
        "instant_exp": 200,
        "exp_list": [],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200
    created_data = create_response.json()

    # 생성된 시즌패스 조회
    response = client.get(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    # Courage Pass는 repeat_last_reward가 True여야 함
    assert data["repeat_last_reward"] == True


def test_get_season_pass_not_found(client, valid_token, test_session):
    """존재하지 않는 시즌패스 조회 테스트"""
    response = client.get(
        "/api/admin/season-passes/99999",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_update_season_pass_success(client, valid_token, test_session):
    """시즌패스 수정 성공 테스트"""
    # 먼저 시즌패스 생성
    season_pass_data = {
        "pass_type": PassType.WORLD_CLEAR_PASS.value,
        "season_index": 3,
        "reward_list": [],
        "instant_exp": 200,
        "exp_list": [],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200
    created_data = create_response.json()

    # 시즌패스 수정 (전체 데이터로 교체)
    update_data = {
        "pass_type": PassType.WORLD_CLEAR_PASS.value,
        "season_index": 3,
        "reward_list": [],
        "instant_exp": 300,
        "exp_list": [
            {"action_type": ActionType.ARENA.value, "exp": 150},
            {"action_type": ActionType.RAID.value, "exp": 200},
        ],
    }

    response = client.put(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=update_data,
    )
    assert response.status_code == 200
    data = response.json()

    assert data["instant_exp"] == 300
    assert len(data["exp_list"]) == 2


def test_update_season_pass_not_found(client, valid_token, test_session):
    """존재하지 않는 시즌패스 수정 테스트"""
    update_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 1,
        "reward_list": [],
        "instant_exp": 300,
        "exp_list": [],
    }

    response = client.put(
        "/api/admin/season-passes/99999",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=update_data,
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_delete_season_pass_success(client, valid_token, test_session):
    """시즌패스 삭제 성공 테스트"""
    # 먼저 시즌패스 생성
    season_pass_data = {
        "pass_type": PassType.COURAGE_PASS.value,
        "season_index": 4,
        "reward_list": [],
        "instant_exp": 100,
        "exp_list": [],
    }

    create_response = client.post(
        "/api/admin/season-passes",
        headers={"Authorization": f"Bearer {valid_token}"},
        json=season_pass_data,
    )
    assert create_response.status_code == 200
    created_data = create_response.json()

    # 시즌패스 삭제
    response = client.delete(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 200
    assert "deleted successfully" in response.json()["message"]

    # 삭제 확인
    get_response = client.get(
        f"/api/admin/season-passes/{created_data['id']}",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert get_response.status_code == 404


def test_delete_season_pass_not_found(client, valid_token, test_session):
    """존재하지 않는 시즌패스 삭제 테스트"""
    response = client.delete(
        "/api/admin/season-passes/99999",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_season_pass_unauthorized(client, test_session):
    """시즌패스 API 인증 실패 테스트"""
    # 인증 헤더 없음
    resp = client.get("/api/admin/season-passes")
    assert resp.status_code == 403

    resp = client.post("/api/admin/season-passes", json={})
    assert resp.status_code == 403

    resp = client.put("/api/admin/season-passes/1", json={})
    assert resp.status_code == 403

    resp = client.delete("/api/admin/season-passes/1")
    assert resp.status_code == 403


# Burn Asset 엔드포인트 테스트
def test_burn_asset_success(client, valid_token, monkeypatch):
    """Burn asset 엔드포인트 성공 테스트"""
    # Mock send_to_worker function
    def mock_send_to_worker(task_name, message):
        assert task_name == "season_pass.process_burn_asset"
        # owner는 worker에서 Account에서 가져오므로 message에 없음
        assert "owner" not in message
        assert message["ticker"] == "NCG"
        assert "decimal_places" not in message  # decimal_places는 제거됨
        assert message["amount"] == "10.5"
        assert message["memo"] == "Test burn asset"
        assert message["planet_id"] == "0x000000000000"  # 기본값
        return "mock_task_id_123"

    monkeypatch.setattr("app.celery.send_to_worker", mock_send_to_worker)

    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"ticker": "NCG", "amount": "10.5", "memo": "Test burn asset"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "mock_task_id_123"
    assert data["status"] == "success"
    assert data["message"] == "Burn asset task triggered successfully"


def test_burn_asset_with_default_values(client, valid_token, monkeypatch):
    """기본값을 사용한 burn asset 테스트"""

    def mock_send_to_worker(task_name, message):
        assert task_name == "season_pass.process_burn_asset"
        # owner는 worker에서 Account에서 가져오므로 message에 없음
        assert "owner" not in message
        assert message["ticker"] == "CRYSTAL"
        assert "decimal_places" not in message  # decimal_places는 제거됨
        assert message["amount"] == "5.0"
        assert message["memo"] == ""  # 기본값
        assert message["planet_id"] == "0x000000000000"  # 기본값
        return "mock_task_id_456"

    monkeypatch.setattr("app.celery.send_to_worker", mock_send_to_worker)

    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"ticker": "CRYSTAL", "amount": "5.0"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "mock_task_id_456"
    assert data["status"] == "success"


def test_burn_asset_with_custom_planet_id(client, valid_token, monkeypatch):
    """사용자 정의 planet_id를 사용한 burn asset 테스트"""

    def mock_send_to_worker(task_name, message):
        assert task_name == "season_pass.process_burn_asset"
        assert message["ticker"] == "NCG"
        assert message["amount"] == "10.5"
        assert "decimal_places" not in message  # decimal_places는 제거됨
        assert message["planet_id"] == "0x000000000001"  # HEIMDALL
        return "mock_task_id_789"

    monkeypatch.setattr("app.celery.send_to_worker", mock_send_to_worker)

    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"ticker": "NCG", "amount": "10.5", "planet_id": "0x000000000001"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "mock_task_id_789"
    assert data["status"] == "success"


def test_burn_asset_unauthorized(client):
    """Burn asset 엔드포인트 인증 실패 테스트"""
    # 인증 헤더 없음
    response = client.post(
        "/api/admin/burn-asset", json={"ticker": "NCG", "amount": "10.5"}
    )
    assert response.status_code == 403

    # 잘못된 토큰
    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": "Bearer invalid_token"},
        json={"ticker": "NCG", "amount": "10.5"},
    )
    assert response.status_code == 401


def test_burn_asset_invalid_request(client, valid_token):
    """잘못된 요청 데이터 테스트"""
    # 필수 필드 누락
    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"amount": "10.5"},
    )
    assert response.status_code == 422

    # 잘못된 amount 타입
    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"ticker": "NCG", "amount": "invalid_amount"},
    )
    assert response.status_code == 422

    # decimal_places 필드가 제거되었으므로 이 테스트는 제거


def test_burn_asset_worker_error(client, valid_token, monkeypatch):
    """Worker task 실행 중 오류 발생 테스트"""

    def mock_send_to_worker(task_name, message):
        raise Exception("Worker task failed")

    monkeypatch.setattr("app.celery.send_to_worker", mock_send_to_worker)

    response = client.post(
        "/api/admin/burn-asset",
        headers={"Authorization": f"Bearer {valid_token}"},
        json={"ticker": "NCG", "amount": "10.5", "memo": "Test burn asset"},
    )

    assert response.status_code == 500
    data = response.json()
    assert "Failed to trigger burn asset task" in data["detail"]


def test_burn_asset_different_currencies(client, valid_token, monkeypatch):
    """다양한 통화에 대한 burn asset 테스트"""
    test_cases = [
        {"ticker": "NCG", "amount": "10.5"},
        {"ticker": "CRYSTAL", "amount": "100"},
        {"ticker": "GOLD", "amount": "0.00000001"},
    ]

    for i, test_case in enumerate(test_cases):

        def mock_send_to_worker(task_name, message):
            assert task_name == "season_pass.process_burn_asset"
            # owner는 worker에서 Account에서 가져오므로 message에 없음
            assert "owner" not in message
            assert message["ticker"] == test_case["ticker"]
            assert "decimal_places" not in message  # decimal_places는 제거됨
            assert message["amount"] == test_case["amount"]
            assert message["planet_id"] == "0x000000000000"  # 기본값
            return f"mock_task_id_{i}"

        monkeypatch.setattr("app.celery.send_to_worker", mock_send_to_worker)

        response = client.post(
            "/api/admin/burn-asset",
            headers={"Authorization": f"Bearer {valid_token}"},
            json=test_case,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
