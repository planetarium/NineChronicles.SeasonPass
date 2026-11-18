"""claim_tx_history 마이그레이션을 실제 Postgres 에 돌려 본다(재서명 B2).

SHARED_TEST_PG_DSN 이 없으면 건너뛴다. 시험마다 public 스키마를 지우고 다시 만들므로 DB 이름에 'test'
가 없으면 시작하지 않는다. 로컬: createdb season_pass_test_b2m 후
SHARED_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/season_pass_test_b2m

alembic 은 따로 띄운다(env.py 가 sys.path·로깅을 바꾸고, 잠금에 걸린 마이그레이션을 시간 안에 끊어야
한다). SHARED_MIGRATION_DIR 로 다른 마이그레이션 디렉터리(고치기 전 사본)를 가리킬 수 있다.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, make_url, text

DSN = os.environ.get("SHARED_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="SHARED_TEST_PG_DSN 이 없음")

SHARED_DIR = Path(
    os.environ.get("SHARED_MIGRATION_DIR", Path(__file__).resolve().parents[1])
)
BASE_REVISION = "bca8f726a5ee"
APP_ROLE = "sp_b2_test_app"
OTHER_ROLE = "sp_b2_test_other"


def ensure_test_database(dsn):
    name = make_url(dsn).database or ""
    if "test" not in name:
        pytest.fail(f"DB 이름 {name!r} 에 'test' 가 없어 스키마를 지우지 않는다")


def role_dsn(role):
    return (
        make_url(DSN)
        .set(username=role, password="pw")
        .render_as_string(hide_password=False)
    )


def alembic(dsn, *args, timeout=60):
    env = dict(os.environ, DATABASE_URI=dsn)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=SHARED_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ready_sql():
    from shared.utils.claim_tx_history import READY_SQL

    return READY_SQL


def is_ready(dsn):
    eng = create_engine(dsn)
    try:
        with eng.connect() as conn:
            return bool(conn.scalar(ready_sql()))
    finally:
        eng.dispose()


@pytest.fixture
def admin():
    ensure_test_database(DSN)
    eng = create_engine(DSN, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for role in (APP_ROLE, OTHER_ROLE):
            if conn.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
            ):
                conn.execute(text(f"DROP OWNED BY {role}"))
                conn.execute(text(f"DROP ROLE {role}"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        for role in (APP_ROLE, OTHER_ROLE):
            conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'pw'"))
    yield eng
    with eng.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        for role in (APP_ROLE, OTHER_ROLE):
            conn.execute(text(f"DROP OWNED BY {role}"))
            conn.execute(text(f"DROP ROLE {role}"))
    eng.dispose()


def seed_rows(dsn):
    eng = create_engine(dsn)
    with eng.begin() as conn:
        for i in range(3):
            conn.execute(
                text(
                    "INSERT INTO claim (uuid, agent_addr, avatar_addr, normal_levels,"
                    " premium_levels, reward_list, planet_id, nonce, created_at, updated_at)"
                    " VALUES (:u, 'a', 'b', '{}', '{}', '[]', '\\x30', :n, now(), now())"
                ),
                {"u": f"u{i}", "n": i},
            )
    eng.dispose()


def counts(dsn):
    eng = create_engine(dsn)
    with eng.connect() as conn:
        result = {
            t: conn.scalar(text(f"SELECT count(*) FROM {t}"))
            for t in ("claim", "season_pass", "user_season_pass")
        }
    eng.dispose()
    return result


# --- 시험 37 -----------------------------------------------------------------------------------


def test_upgrade_downgrade_upgrade_as_app_role(admin):
    app = role_dsn(APP_ROLE)
    r = alembic(app, "upgrade", BASE_REVISION)
    assert r.returncode == 0, r.stderr
    seed_rows(app)
    before = counts(app)

    for step in (("upgrade", "head"), ("downgrade", "-1"), ("upgrade", "head")):
        r = alembic(app, *step)
        assert r.returncode == 0, r.stderr

    assert counts(app) == before  # 기존 테이블 행 수 불변
    assert is_ready(app) is True  # 앱과 같은 역할로 돌리면 바로 준비됨
    # 다른 역할에는 권한이 없다 → 문서의 GRANT 를 하면 준비된다.
    assert is_ready(role_dsn(OTHER_ROLE)) is False
    eng = create_engine(app)
    with eng.begin() as conn:
        conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE ON claim_tx_history TO {OTHER_ROLE}")
        )
        conn.execute(
            text(f"GRANT USAGE ON SEQUENCE claim_tx_history_id_seq TO {OTHER_ROLE}")
        )
    eng.dispose()
    assert is_ready(role_dsn(OTHER_ROLE)) is True


def test_ready_sql_checks_each_privilege_separately(admin):
    app = role_dsn(APP_ROLE)
    assert alembic(app, "upgrade", "head").returncode == 0
    other = role_dsn(OTHER_ROLE)
    eng = create_engine(app)
    with eng.begin() as conn:
        conn.execute(text(f"GRANT SELECT ON claim_tx_history TO {OTHER_ROLE}"))
        conn.execute(
            text(f"GRANT USAGE ON SEQUENCE claim_tx_history_id_seq TO {OTHER_ROLE}")
        )
        # 쉼표로 묶어 물으면 "하나라도 있으면 true" 라 통과해 버린다 — 그래서 하나씩 묻는다.
    eng.dispose()
    probe = create_engine(other)
    with probe.connect() as conn:
        combined = conn.scalar(
            text("SELECT has_table_privilege('claim_tx_history', 'SELECT, INSERT')")
        )
    probe.dispose()
    assert combined is True
    assert is_ready(other) is False


# --- 시험 38: 잠금 ---------------------------------------------------------------------------------


def test_upgrade_gives_up_on_lock_instead_of_queueing_writes(admin):
    """다른 연결이 claim 에 ROW EXCLUSIVE(쓰기 트랜잭션)를 쥐고 있을 때.

    FK 를 만드는 CREATE TABLE 은 claim 에 SHARE ROW EXCLUSIVE 를 기다리고, 그 뒤에 온 INSERT 는
    마이그레이션 뒤에 줄을 선다. lock_timeout 5초가 있으면 마이그레이션이 실패하고 INSERT 는 그때 풀린다.
    없으면 쥔 쪽이 끝날 때까지 둘 다 막힌다. 시험 상한은 8초다."""
    app = role_dsn(APP_ROLE)
    assert alembic(app, "upgrade", BASE_REVISION).returncode == 0
    seed_rows(app)
    eng = create_engine(app)
    holder = eng.connect()
    holder.execute(text("UPDATE claim SET updated_at = updated_at WHERE nonce = 0"))

    migration = {}

    def migrate():
        try:
            migration["result"] = alembic(app, "upgrade", "head", timeout=30)
        except subprocess.TimeoutExpired as e:
            migration["timeout"] = e

    t_migrate = threading.Thread(target=migrate, daemon=True)
    t_migrate.start()
    time.sleep(1.0)  # 마이그레이션이 claim 잠금을 기다리기 시작한다

    insert = {}

    def write():
        started = time.monotonic()
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO claim (uuid, agent_addr, avatar_addr, normal_levels,"
                    " premium_levels, reward_list, planet_id, created_at, updated_at)"
                    " VALUES ('api', 'a', 'b', '{}', '{}', '[]', '\\x30', now(), now())"
                )
            )
        insert["elapsed"] = time.monotonic() - started

    t_write = threading.Thread(target=write, daemon=True)
    t_write.start()
    t_write.join(8)
    try:
        assert not t_write.is_alive(), "API 의 INSERT 가 8초 넘게 마이그레이션 뒤에 막혔다"
        t_migrate.join(10)
        result = migration.get("result")
        assert result is not None and result.returncode != 0
        assert "lock timeout" in result.stderr or "55P03" in result.stderr
    finally:
        holder.rollback()
        holder.close()
        t_migrate.join(35)
        t_write.join(5)
        eng.dispose()

    # 쥔 쪽이 끝난 뒤 다시 돌리면 된다.
    assert alembic(app, "upgrade", "head").returncode == 0
    assert is_ready(app) is True


def test_refuses_database_without_test_in_name():
    with pytest.raises(pytest.fail.Exception):
        ensure_test_database("postgresql://u:p@db:5432/season_pass")
