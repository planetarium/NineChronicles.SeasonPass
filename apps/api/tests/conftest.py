# isort: skip_file
from datetime import datetime, timedelta, timezone

import jwt
import pytest

# app 설정 관련 import
from app.config import Settings, config
from app.dependencies import session
from fastapi.testclient import TestClient
from shared.enums import PassType, PlanetID, TxStatus

# 먼저 shared 모듈 import
from shared.models.base import Base
from shared.models.user import Claim, SeasonPass, UserSeasonPass
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session", autouse=True)
def app_config():
    """애플리케이션 설정을 초기화하는 fixture"""
    settings = Settings()
    config.jwt_secret = TEST_JWT_SECRET
    return settings


# FastAPI app import
from main import app

# 테스트용 상수
TEST_AGENT_ADDR = "0x1234567890abcdef"
TEST_AVATAR_ADDR = "0x0987654321fedcba"
TEST_JWT_SECRET = "test_secret_key"

# 테스트 DB 설정
TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/season_pass_test"
engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def test_db():
    # 테스트 DB 생성
    Base.metadata.create_all(bind=engine)
    yield
    # 테스트 DB 삭제
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_session(test_db):
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(test_session):
    # DB 세션 오버라이드
    def override_get_session():
        try:
            yield test_session
        finally:
            test_session.close()

    app.dependency_overrides[session] = override_get_session

    with TestClient(app) as client:
        yield client


@pytest.fixture
def valid_token():
    """유효한 JWT 토큰 생성"""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "admin",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "aud": "SeasonPass",
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def test_claims(test_session):
    """테스트용 Claim 데이터 생성"""
    now = datetime.now(tz=timezone.utc)
    claims = [
        Claim(
            uuid="claim1",
            agent_addr=TEST_AGENT_ADDR,
            avatar_addr=TEST_AVATAR_ADDR,
            planet_id=PlanetID.ODIN,
            tx_status=TxStatus.SUCCESS,
            reward_list=[{"item": "test_item"}],
            created_at=now - timedelta(days=1),
        ),
        Claim(
            uuid="claim2",
            agent_addr=TEST_AGENT_ADDR,
            avatar_addr=TEST_AVATAR_ADDR,
            planet_id=PlanetID.HEIMDALL,
            tx_status=TxStatus.FAILURE,
            reward_list=[{"item": "test_item2"}],
            created_at=now - timedelta(days=2),
        ),
    ]

    for claim in claims:
        test_session.add(claim)
    test_session.commit()

    return claims


@pytest.fixture
def test_users(test_session):
    """테스트용 UserSeasonPass 데이터 생성"""
    # 먼저 SeasonPass 레코드 생성
    now = datetime.now(tz=timezone.utc)
    season_passes = [
        SeasonPass(
            id=1,
            pass_type=PassType.COURAGE_PASS,
            season_index=1,
            start_timestamp=now - timedelta(days=1),
            end_timestamp=now + timedelta(days=1),
            instant_exp=100,
            reward_list=[{"item": "test_item"}],
        ),
        SeasonPass(
            id=2,
            pass_type=PassType.COURAGE_PASS,
            season_index=2,
            start_timestamp=now - timedelta(days=1),
            end_timestamp=now + timedelta(days=1),
            instant_exp=100,
            reward_list=[{"item": "test_item"}],
        ),
    ]

    for season_pass in season_passes:
        test_session.add(season_pass)
    test_session.commit()

    users = [
        UserSeasonPass(
            planet_id=PlanetID.ODIN,
            agent_addr=TEST_AGENT_ADDR,
            avatar_addr=TEST_AVATAR_ADDR,
            season_pass_id=1,
            is_premium=True,
        ),
        UserSeasonPass(
            planet_id=PlanetID.HEIMDALL,
            agent_addr=f"{TEST_AGENT_ADDR}2",
            avatar_addr=f"{TEST_AVATAR_ADDR}2",
            season_pass_id=2,
            is_premium=False,
        ),
    ]

    for user in users:
        test_session.add(user)
    test_session.commit()

    return users
