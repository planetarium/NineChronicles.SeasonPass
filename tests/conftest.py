import os
from contextlib import contextmanager
from typing import List

import alembic.command
import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

TEST_AGENT_ADDR = "0x49D5FcEB955800B2c532D6319E803c7D80f817Af"
TEST_AVATAR_ADDR = "0xC8cA85aE399de5c4dcAd39e8A13cfA7cBcEff066"


@pytest.fixture(scope="session", autouse=True)
def setup_alembic():
    config = Config("common/alembic.ini")
    config.set_main_option("script_location", "common/alembic")
    config.set_main_option("sqlalchemy.url", os.environ.get('DB_URI'))
    try:
        alembic.command.upgrade(config, "head")
        alembic.command.history(config)
        alembic.command.current(config)
        yield
    finally:
        alembic.command.downgrade(config, "base")


@pytest.mark.usefixtures("setup_alembic")
@pytest.fixture(scope="session")
def engine(setup_alembic):
    engine = create_engine(os.environ.get("DB_URI"))
    yield engine


@pytest.fixture(scope="session")
@pytest.mark.usefixtures("engine")
def session(engine):
    sess = scoped_session(sessionmaker(bind=engine))
    try:
        yield sess
    finally:
        sess.close()


# @pytest.fixture(scope="session", autouse=True)
# @pytest.mark.usefixtures("session")
# def set_test_data(session):
#     with open("tests/data/level.json", "r") as f:
#         level_data = json.loads(f.read())
#     for lvl in level_data:
#         session.add(Level(level=lvl["level"], exp=lvl["exp"]))
#
#     with open("tests/data/reward.json", "r") as f:
#         reward = json.loads(f.read())
#
#     start_timestamp = datetime.datetime.now(tz=datetime.timezone.utc).replace(day=1)
#     session.add(
#         SeasonPass(id=1, start_timestamp=start_timestamp, end_timestamp=start_timestamp + datetime.timedelta(days=28),
#                    reward_list=reward, instant_exp=10000)
#     )
#
#     session.commit()
#
#
# @pytest.fixture(scope="session")
# @pytest.mark.usefixtures("session")
# def new_user(session):
#     nu = UserSeasonPass(agent_addr=TEST_AGENT_ADDR, avatar_addr=TEST_AVATAR_ADDR, season_pass_id=1,
#                         planet_id=PlanetID.ODIN_INTERNAL)
#     session.add(nu)
#     session.commit()
#     yield nu
#     session.delete(nu)
#     session.commit()


@pytest.fixture(scope="function")
@pytest.mark.usefixtures("engine")
def sess(engine):
    s = scoped_session(sessionmaker(bind=engine))
    try:
        yield s
    finally:
        s.close()


@contextmanager
def add_test_data(sess, *test_data: List) -> List:
    updated_data = []
    try:
        for data in test_data:
            sess.add(data)
        sess.commit()
        for data in test_data:
            sess.refresh(data)
            updated_data.append(data)
        yield updated_data
    finally:
        for data in updated_data:
            sess.delete(data)
        sess.commit()
