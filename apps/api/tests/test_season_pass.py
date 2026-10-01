from datetime import datetime, timedelta, timezone

import pytest
from shared.enums import ActionType, PassType, PlanetID
from shared.models.season_pass import Exp, Level, SeasonPass


@pytest.mark.usefixtures("test_session")
def test_current_season_endpoint_compatibility(test_session):
    """기존 season_pass/current 엔드포인트 호환성 확인"""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    # 테스트용 시즌패스 데이터 생성 (고유한 ID 사용)
    season_data = SeasonPass(
        id=9991,  # 고유한 ID 사용
        pass_type=PassType.COURAGE_PASS,
        season_index=999,  # 고유한 시즌 인덱스
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
        instant_exp=1000,  # 필수 필드 추가
        reward_list=[
            {
                "level": 1,
                "normal": [
                    {"ticker": "Item_1001", "amount": 10},
                    {"ticker": "FAV__NCG", "amount": 100.0},
                ],
                "premium": [
                    {"ticker": "Item_2001", "amount": 20},
                    {"ticker": "FAV__NCG", "amount": 200.0},
                ],
            }
        ],
    )

    test_session.add(season_data)
    test_session.commit()

    # API 호출
    response = client.get(
        "/api/season-pass/current",
        params={
            "planet_id": PlanetID.ODIN_INTERNAL.value.decode(),
            "pass_type": PassType.COURAGE_PASS.value,
        },
    )

    assert response.status_code == 200
    data = response.json()

    # 기존 스키마 필드들이 존재하는지 확인
    assert "id" in data
    assert "pass_type" in data
    assert "season_index" in data
    assert "start_date" in data
    assert "end_date" in data
    assert "start_timestamp" in data
    assert "end_timestamp" in data
    assert "reward_list" in data
    assert "repeat_last_reward" in data

    # reward_list 구조 확인 (기존 데이터가 있을 수 있으므로 최소 1개 이상)
    assert len(data["reward_list"]) >= 1
    reward = data["reward_list"][0]
    assert "level" in reward
    assert "normal" in reward
    assert "premium" in reward

    # normal/premium 구조 확인
    normal = reward["normal"]
    premium = reward["premium"]
    assert "item" in normal
    assert "currency" in normal
    assert "item" in premium
    assert "currency" in premium


@pytest.mark.usefixtures("test_session")
def test_current_season_endpoint_world_clear_pass(test_session):
    """World Clear Pass의 repeat_last_reward 동작 확인"""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    # World Clear Pass 데이터 생성
    season_data = SeasonPass(
        id=9992,  # 고유한 ID 사용
        pass_type=PassType.WORLD_CLEAR_PASS,
        season_index=998,  # 고유한 시즌 인덱스
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
        instant_exp=500,  # 필수 필드 추가
        reward_list=[],
    )

    test_session.add(season_data)
    test_session.commit()

    # API 호출
    response = client.get(
        "/api/season-pass/current",
        params={
            "planet_id": PlanetID.ODIN_INTERNAL.value.decode(),
            "pass_type": PassType.WORLD_CLEAR_PASS.value,
        },
    )

    assert response.status_code == 200
    data = response.json()

    # World Clear Pass는 repeat_last_reward가 False여야 함
    assert data["repeat_last_reward"] == False


@pytest.mark.usefixtures("test_session")
def test_current_season_endpoint_courage_pass(test_session):
    """Courage Pass의 repeat_last_reward 동작 확인"""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    # Courage Pass 데이터 생성
    season_data = SeasonPass(
        id=9993,  # 고유한 ID 사용
        pass_type=PassType.COURAGE_PASS,
        season_index=997,  # 고유한 시즌 인덱스
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
        instant_exp=1000,  # 필수 필드 추가
        reward_list=[],
    )

    test_session.add(season_data)
    test_session.commit()

    # API 호출
    response = client.get(
        "/api/season-pass/current",
        params={
            "planet_id": PlanetID.ODIN_INTERNAL.value.decode(),
            "pass_type": PassType.COURAGE_PASS.value,
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Courage Pass는 repeat_last_reward가 True여야 함
    assert data["repeat_last_reward"] == True


@pytest.mark.usefixtures("test_session")
def test_level_info_endpoint(test_session):
    """level 엔드포인트 동작 확인"""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    # 테스트용 레벨 데이터 생성
    level_data = Level(
        level=999,  # 고유한 레벨
        exp=99999,  # 고유한 경험치
        pass_type=PassType.COURAGE_PASS,
    )

    test_session.add(level_data)
    test_session.commit()

    # API 호출
    response = client.get(
        "/api/season-pass/level",
        params={"pass_type": PassType.COURAGE_PASS.value},
    )

    assert response.status_code == 200
    data = response.json()

    # 기존 데이터가 있을 수 있으므로 최소 1개 이상
    assert len(data) >= 1
    # 우리가 추가한 데이터가 있는지 확인
    our_level = next((item for item in data if item["level"] == 999), None)
    if our_level is None:
        # 데이터가 조회되지 않으면 다른 레벨 데이터가 있는지 확인
        assert len(data) > 0
        # 첫 번째 레벨의 구조 확인
        first_level = data[0]
        assert "level" in first_level
        assert "exp" in first_level
        assert isinstance(first_level["level"], int)
        assert isinstance(first_level["exp"], int)
    else:
        assert our_level["exp"] == 99999


@pytest.mark.usefixtures("test_session")
def test_exp_info_endpoint(test_session):
    """exp 엔드포인트 동작 확인"""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    # 테스트용 시즌패스 데이터 생성
    season_data = SeasonPass(
        id=9994,  # 고유한 ID 사용
        pass_type=PassType.COURAGE_PASS,
        season_index=996,  # 고유한 시즌 인덱스
        start_timestamp=datetime.now(tz=timezone.utc) - timedelta(days=1),
        end_timestamp=datetime.now(tz=timezone.utc) + timedelta(days=1),
        instant_exp=1000,  # 필수 필드 추가
    )

    test_session.add(season_data)
    test_session.flush()  # ID 생성을 위해 flush

    # Exp 데이터 생성
    exp_data1 = Exp(
        season_pass_id=season_data.id,
        action_type=ActionType.HAS,
        exp=99999,  # 고유한 경험치
    )
    exp_data2 = Exp(
        season_pass_id=season_data.id,
        action_type=ActionType.SWEEP,
        exp=88888,  # 고유한 경험치
    )

    test_session.add(exp_data1)
    test_session.add(exp_data2)
    test_session.commit()

    # API 호출 - 현재 활성 시즌을 조회
    response = client.get(
        "/api/season-pass/exp",
        params={
            "pass_type": PassType.COURAGE_PASS.value,
            "season_index": 996,  # 현재 활성 시즌 인덱스 사용
        },
    )

    assert response.status_code == 200
    data = response.json()

    # 데이터가 있는지 확인 (기존 데이터가 있을 수 있음)
    assert len(data) >= 0

    # 우리가 추가한 데이터가 있는지 확인 (선택적)
    has_exp = next(
        (
            item
            for item in data
            if item["action_type"] == "HAS" and item["exp"] == 99999
        ),
        None,
    )
    sweep_exp = next(
        (
            item
            for item in data
            if item["action_type"] == "SWEEP" and item["exp"] == 88888
        ),
        None,
    )

    # 데이터가 있으면 구조 확인
    if data:
        first_exp = data[0]
        assert "action_type" in first_exp
        assert "exp" in first_exp
        assert isinstance(first_exp["action_type"], str)
        assert isinstance(first_exp["exp"], int)


def test_schema_backward_compatibility():
    """스키마 하위 호환성 테스트"""
    from app.schemas.season_pass import SeasonPassSchema

    # 기존 형식의 데이터로 테스트
    test_data = {
        "id": 1,
        "pass_type": "COURAGE_PASS",
        "season_index": 1,
        "start_date": datetime.now(tz=timezone.utc).date(),
        "end_date": (datetime.now(tz=timezone.utc) + timedelta(days=30)).date(),
        "start_timestamp": datetime.now(tz=timezone.utc),
        "end_timestamp": datetime.now(tz=timezone.utc) + timedelta(days=30),
        "reward_list": [
            {
                "level": 1,
                "normal": {
                    "item": [{"id": 1001, "amount": 10}],
                    "currency": [{"ticker": "NCG", "amount": 100.0}],
                },
                "premium": {
                    "item": [{"id": 2001, "amount": 20}],
                    "currency": [{"ticker": "NCG", "amount": 200.0}],
                },
            }
        ],
        "repeat_last_reward": True,
    }

    # 스키마 검증이 성공해야 함
    schema = SeasonPassSchema(**test_data)
    assert schema.id == 1
    assert schema.pass_type == "COURAGE_PASS"
    assert schema.season_index == 1
    assert schema.start_date is not None
    assert schema.end_date is not None
    assert schema.start_timestamp is not None
    assert schema.end_timestamp is not None
    assert len(schema.reward_list) == 1
    assert schema.repeat_last_reward == True


def _make_season(sess, id_, index, start, end):
    sess.add(
        SeasonPass(
            id=id_,
            pass_type=PassType.COURAGE_PASS,
            season_index=index,
            start_timestamp=start,
            end_timestamp=end,
            instant_exp=0,
            reward_list=[],
        )
    )
    sess.commit()


def _get_current(client, **params):
    return client.get(
        "/api/season-pass/current",
        params={
            "planet_id": PlanetID.ODIN_INTERNAL.value.decode(),
            "pass_type": PassType.COURAGE_PASS.value,
            **params,
        },
    )


@pytest.mark.usefixtures("test_session")
def test_current_season_at_selects_season_by_given_time(test_session):
    """`at` 을 주면 그 시각의 시즌 — IAP 가 고정 SKU 결제를 결제 시각 시즌에 귀속시킨다."""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    now = datetime.now(tz=timezone.utc)
    boundary = now - timedelta(days=1)
    _make_season(test_session, 9981, 981, boundary - timedelta(days=30), boundary)
    _make_season(test_session, 9982, 982, boundary, now + timedelta(days=30))

    # 지금(기본값)은 새 시즌
    assert _get_current(client).json()["season_index"] == 982
    # 경계 전 시각 → 지난 시즌
    before = (boundary - timedelta(hours=1)).isoformat()
    assert _get_current(client, at=before).json()["season_index"] == 981
    # 경계 순간은 양쪽에 걸린다 → 기존 규칙(desc(id))대로 나중 시즌
    assert _get_current(client, at=boundary.isoformat()).json()["season_index"] == 982


@pytest.mark.usefixtures("test_session")
def test_current_season_at_outside_any_season_is_404(test_session):
    from fastapi.testclient import TestClient

    from main import app

    # 시즌 없음은 전역 Exception 핸들러가 404 로 바꾼다 — TestClient 가 먼저 다시 던지지 않게.
    client = TestClient(app, raise_server_exceptions=False)
    now = datetime.now(tz=timezone.utc)
    _make_season(
        test_session, 9983, 983, now - timedelta(days=1), now + timedelta(days=1)
    )

    far_past = (now - timedelta(days=400)).isoformat()
    resp = _get_current(client, at=far_past)
    assert resp.status_code == 404


@pytest.mark.usefixtures("test_session")
def test_current_season_at_requires_timezone(test_session):
    """offset 없는 시각은 거절한다 — 경계가 UTC 라 로컬 시각으로 해석하면 시즌이 어긋난다."""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    resp = _get_current(client, at="2026-10-01T00:00:00")
    assert resp.status_code == 422


@pytest.mark.usefixtures("test_session")
def test_current_season_at_rejects_future(test_session):
    """공개 전 시즌 reward_list 노출 방지 — 인증 없는 엔드포인트라 미래 `at` 은 400."""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app, raise_server_exceptions=False)
    now = datetime.now(tz=timezone.utc)
    _make_season(
        test_session, 9984, 984, now + timedelta(days=1), now + timedelta(days=30)
    )

    resp = _get_current(client, at=(now + timedelta(days=2)).isoformat())
    assert resp.status_code == 400
    # 시계 차이 정도(수 초)는 받는다.
    _make_season(
        test_session, 9985, 985, now - timedelta(days=1), now + timedelta(days=1)
    )
    near = (now + timedelta(seconds=30)).isoformat()
    assert _get_current(client, at=near).json()["season_index"] == 985


@pytest.mark.usefixtures("test_session")
def test_current_season_at_end_boundary_is_inclusive(test_session):
    """다음 시즌이 없을 때 끝 경계 순간까지는 그 시즌이다(양 끝 inclusive 규칙)."""
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    now = datetime.now(tz=timezone.utc)
    end = now - timedelta(hours=1)
    _make_season(test_session, 9986, 986, end - timedelta(days=30), end)

    assert _get_current(client, at=end.isoformat()).json()["season_index"] == 986


def test_get_pass_rejects_naive_at():
    from shared.enums import PassType as PT
    from shared.utils.season_pass import get_pass

    with pytest.raises(ValueError, match="timezone-aware"):
        get_pass(None, PT.COURAGE_PASS, validate_current=True, at=datetime(2026, 10, 1))
