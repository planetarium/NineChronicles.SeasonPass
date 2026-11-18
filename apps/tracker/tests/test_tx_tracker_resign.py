"""track_tx — 재서명(B2) 모드 게이트와 옛 tx 확정.

- process 의 상태 매핑은 노드 응답을 흉내 내 확인한다(DB 없음).
- 창 순환·기록 확정·권한·테이블 없음은 실제 Postgres 로 본다(TRACKER_TEST_PG_DSN, DB 이름에 'test').

새 이름은 모듈 속성으로 부르고 config 는 __dict__ 로 바꾼다. 고치기 전 코드에서도 모듈을 읽고 각 시험이
동작으로 실패하게 하려는 것이다.
"""
import hashlib
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.config import config
from app.trackers import tx_tracker
from gql.transport.exceptions import TransportQueryError
from shared.enums import PlanetID, TxStatus
from shared.models.base import Base
from shared.models.user import Claim
from shared.utils import claim_tx_history as history_utils
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

DSN = os.environ.get("TRACKER_TEST_PG_DSN")
ODIN = PlanetID.ODIN
URL_MAP = {ODIN.value.decode(): "http://odin"}


def set_config(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setitem(config.__dict__, k, v)


# --- 시험 32: 상태 매핑(DB 없음) -------------------------------------------------------------------


def run_process(monkeypatch, mode, response):
    set_config(monkeypatch, resign_mode=mode, gql_url_map=URL_MAP)
    client = MagicMock()
    if isinstance(response, Exception):
        client.execute.side_effect = response
    else:
        client.execute.return_value = {
            "transaction": {
                "transactionResult": {"txStatus": response, "exceptionNames": None}
            }
        }
    with patch.object(tx_tracker, "GQLClient", return_value=client), patch.object(
        tx_tracker, "dsl_gql"
    ), patch.object(tx_tracker, "DSLQuery"), patch.object(tx_tracker, "logger"):
        result = tx_tracker.process(ODIN, "tx1")
    return result[1]


@pytest.mark.parametrize(
    "response,on,off",
    [
        ("SUCCESS", TxStatus.SUCCESS, TxStatus.SUCCESS),
        ("FAILURE", TxStatus.FAILURE, TxStatus.FAILURE),
        ("INVALID", TxStatus.INVALID, TxStatus.INVALID),
        ("STAGING", TxStatus.STAGED, TxStatus.INVALID),
        ("INCLUDED", None, TxStatus.INVALID),
        ("SOMETHING_NEW", None, TxStatus.INVALID),
    ],
)
def test_status_mapping_by_mode(monkeypatch, response, on, off):
    assert run_process(monkeypatch, "on", response) == on
    assert run_process(monkeypatch, "off", response) == off
    assert run_process(monkeypatch, "dry_run", response) == off  # dry_run 쓰기는 off 와 같다


def test_graphql_errors_keep_status_only_in_on(monkeypatch):
    err = TransportQueryError("tx not found", errors=[{"message": "x"}])
    assert run_process(monkeypatch, "on", err) is None
    with pytest.raises(TransportQueryError):
        run_process(monkeypatch, "off", err)  # 지금처럼 조회 실패(쓰지 않음)


def test_settle_statement_never_sets_nonce():
    stmt = tx_tracker.settle_stmt(1, "cur", "win", "beef", TxStatus.SUCCESS)
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert "nonce" not in str(compiled).split(" WHERE ")[0]
    assert set(compiled.params) >= {"tx_id", "tx", "tx_status", "id_1", "tx_id_1"}


# --- 실 DB --------------------------------------------------------------------------------------

needs_db = pytest.mark.skipif(not DSN, reason="TRACKER_TEST_PG_DSN 이 없음")


def ensure_test_database(dsn):
    name = make_url(dsn).database or ""
    if "test" not in name:
        pytest.fail(f"DB 이름 {name!r} 에 'test' 가 없어 drop_all 을 하지 않는다")


@pytest.fixture(scope="module")
def engine():
    ensure_test_database(DSN)
    eng = create_engine(DSN, pool_size=10)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine, monkeypatch):
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE claim, claim_tx_history RESTART IDENTITY CASCADE"))
    reset = getattr(history_utils, "reset_history_table_ready_cache", None)
    if reset:
        reset()
    set_config(monkeypatch, gql_url_map=URL_MAP, resign_mode="off")
    return engine


def tx_of(name):
    tx = name.encode().hex()
    return tx, hashlib.sha256(name.encode()).hexdigest()


def add_claim(db, n, status=TxStatus.INVALID, name=None):
    tx, tx_id = tx_of(name or f"tx-{n}")
    with Session(db) as s:
        c = Claim(
            uuid=f"c-{n}",
            agent_addr="0x" + "aa" * 20,
            avatar_addr="0x" + "bb" * 20,
            planet_id=ODIN.value,
            normal_levels=[1],
            premium_levels=[],
            reward_list=[{"ticker": "CRYSTAL", "amount": 1}],
            nonce=n,
            tx=tx,
            tx_id=tx_id,
            tx_status=status,
        )
        s.add(c)
        s.commit()
        return SimpleNamespace(id=c.id, tx=tx, tx_id=tx_id, nonce=n)


def add_history(db, claim, name, reason="expired"):
    tx, tx_id = tx_of(name)
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_tx_history (claim_id, planet_id, nonce, tx_id, tx, reason,"
                " created_at, updated_at) VALUES (:c, :p, :n, :i, :t, :r, now(), now())"
            ),
            {
                "c": claim.id,
                "p": ODIN.value,
                "n": claim.nonce,
                "i": tx_id,
                "t": tx,
                "r": reason,
            },
        )
    return SimpleNamespace(tx=tx, tx_id=tx_id)


def row(db, cid):
    with Session(db) as s:
        c = s.get(Claim, cid)
        return SimpleNamespace(
            tx=c.tx,
            tx_id=c.tx_id,
            tx_status=c.tx_status,
            nonce=c.nonce,
            updated_at=c.updated_at,
        )


def history(db):
    with db.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT claim_id, nonce, tx_id, reason FROM claim_tx_history ORDER BY id"
                )
            )
        ]


class FakeNode:
    """tx_id → 노드 상태 이름(또는 예외). 조회한 tx_id 를 기록한다."""

    def __init__(self, statuses=None, default="INVALID"):
        self.statuses = statuses or {}
        self.default = default
        self.asked = []

    def __call__(self, planet_id, tx_id):
        self.asked.append(tx_id)
        raw = self.statuses.get(tx_id, self.default)
        if isinstance(raw, Exception):
            raise raw
        if not hasattr(tx_tracker, "Lookup"):
            # 고치기 전 process 의 반환 모양(모드 없음): TxStatus 에 없는 이름은 INVALID, errors 는 예외.
            if raw == "errors":
                raise TransportQueryError("errors")
            return tx_id, TxStatus.__members__.get(raw, TxStatus.INVALID), "null"
        mode = config.resign_mode
        if raw == "errors":
            if mode == "on":
                return tx_tracker.Lookup(tx_id, None, "errors", "errors")
            raise TransportQueryError("errors")
        return tx_tracker.Lookup(tx_id, tx_tracker.map_status(raw, mode), "null", raw)


def track(db, node, ready=None):
    patches = [
        patch.object(tx_tracker, "engine", db),
        patch.object(tx_tracker, "process", node),
        patch.object(tx_tracker, "logger"),
    ]
    if ready is not None:
        patches.append(
            patch.object(
                tx_tracker, "history_table_ready", return_value=ready, create=True
            )
        )
    for p in patches:
        p.start()
    try:
        tx_tracker.track_tx()
        return tx_tracker.logger
    finally:
        for p in reversed(patches):
            p.stop()


# --- 시험 33·34: 옛 tx 확정 ---------------------------------------------------------------------


@needs_db
@pytest.mark.parametrize("mode", ["off", "dry_run", "on"])
def test_earlier_tx_that_made_it_in_settles_the_claim(db, monkeypatch, mode):
    set_config(monkeypatch, resign_mode=mode)
    c = add_claim(db, 10, name="T-prime")
    old = add_history(db, c, "T")
    released = add_history(db, c, "T-released", reason="released")
    done = add_claim(db, 11, status=TxStatus.SUCCESS)
    node = FakeNode({old.tx_id: "SUCCESS"})

    track(db, node)

    r = row(db, c.id)
    assert (r.tx_id, r.tx, r.tx_status, r.nonce) == (
        old.tx_id,
        old.tx,
        TxStatus.SUCCESS,
        10,
    )
    assert (c.id, 10, c.tx_id, "lost") in history(db)
    assert released.tx_id not in node.asked  # released 기록은 보지 않는다
    assert done.tx_id not in node.asked
    assert row(db, done.id).tx_status == TxStatus.SUCCESS


@needs_db
def test_current_success_wins_without_history_lookup_effect(db):
    c = add_claim(db, 10, name="T-prime")
    add_history(db, c, "T")
    node = FakeNode({c.tx_id: "SUCCESS"})

    track(db, node)

    r = row(db, c.id)
    assert (r.tx_id, r.tx_status) == (c.tx_id, TxStatus.SUCCESS)
    assert [h for h in history(db) if h[3] == "lost"] == []


@needs_db
def test_created_row_settled_by_history_after_resign(db):
    # 재서명 직후 stage 전(CREATED)에 옛 tx 가 이긴 경우: 다음 주기에 STAGED/INVALID 가 되면 확정된다.
    c = add_claim(db, 10, status=TxStatus.STAGED, name="T-prime")
    old = add_history(db, c, "T")
    node = FakeNode({old.tx_id: "FAILURE"})
    track(db, node)
    assert row(db, c.id).tx_status == TxStatus.FAILURE
    assert row(db, c.id).tx_id == old.tx_id


# --- 시험 32(쓰기): on 의 None 은 updated_at 만, 네트워크 예외는 아무것도 ------------------------------


@needs_db
def test_on_writes_staged_and_touches_unknown(db, monkeypatch):
    set_config(monkeypatch, resign_mode="on")
    staging = add_claim(db, 1)
    included = add_claim(db, 2)
    errors = add_claim(db, 3)
    down = add_claim(db, 4)
    with db.begin() as conn:
        conn.execute(text("UPDATE claim SET updated_at = now() - interval '1 hour'"))
    before = {c.id: row(db, c.id).updated_at for c in (staging, included, errors, down)}
    node = FakeNode(
        {
            staging.tx_id: "STAGING",
            included.tx_id: "INCLUDED",
            errors.tx_id: "errors",
            down.tx_id: TimeoutError("node down"),
        }
    )

    track(db, node)

    assert row(db, staging.id).tx_status == TxStatus.STAGED
    for c in (included, errors):
        r = row(db, c.id)
        assert r.tx_status == TxStatus.INVALID  # 상태는 그대로
        assert r.updated_at > before[c.id]  # updated_at 만 갱신
    r = row(db, down.id)
    assert (r.tx_status, r.updated_at) == (TxStatus.INVALID, before[down.id])


@needs_db
@pytest.mark.parametrize("mode", ["off", "dry_run"])
def test_off_and_dry_run_write_invalid_as_before(db, monkeypatch, mode):
    set_config(monkeypatch, resign_mode=mode)
    staging = add_claim(db, 1, status=TxStatus.STAGED)
    included = add_claim(db, 2, status=TxStatus.STAGED)
    node = FakeNode({staging.tx_id: "STAGING", included.tx_id: "INCLUDED"})

    logger = track(db, node)

    assert row(db, staging.id).tx_status == TxStatus.INVALID
    assert row(db, included.id).tx_status == TxStatus.INVALID
    summary = [
        c.kwargs
        for c in logger.info.call_args_list
        if c.args and c.args[0] == "Tx tracker resign summary"
    ]
    if mode == "dry_run":
        assert summary[0]["planets"][ODIN.value]["would_staged"] == 1
        assert summary[0]["planets"][ODIN.value]["included"] == 1
    else:
        assert summary == []


# --- 시험 35: on 의 창 순환 ----------------------------------------------------------------------


def fill_window(db, count=100):
    for n in range(1, count + 1):
        add_claim(db, n)
    return add_claim(db, count + 1)


@needs_db
@pytest.mark.parametrize("stuck", ["INVALID", "INCLUDED"])
def test_on_window_rotates_past_stuck_claims(db, monkeypatch, stuck):
    set_config(monkeypatch, resign_mode="on")
    behind = fill_window(db)  # id 순 앞쪽에 영구히 안 풀리는 100건
    with db.begin() as conn:
        conn.execute(text("UPDATE claim SET updated_at = now() - interval '1 hour'"))
    node = FakeNode({behind.tx_id: "SUCCESS"}, default=stuck)

    for _ in range(3):
        track(db, node)
        if row(db, behind.id).tx_status == TxStatus.SUCCESS:
            break

    assert row(db, behind.id).tx_status == TxStatus.SUCCESS


@needs_db
def test_off_window_keeps_id_order(db):
    behind = fill_window(db)
    node = FakeNode({behind.tx_id: "SUCCESS"})
    for _ in range(3):
        track(db, node)
    assert behind.tx_id not in node.asked  # 지금 동작(off)은 그대로


# --- 시험 36: 기록 읽기 실패 ---------------------------------------------------------------------

ROLE = "sp_b2_test_tracker_noacl"


@pytest.fixture
def no_history_select(db):
    with db.begin() as conn:
        if conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE}):
            conn.execute(text(f"DROP OWNED BY {ROLE}"))
            conn.execute(text(f"DROP ROLE {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD 'pw'"))
        conn.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {ROLE}"))
        conn.execute(text(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"))
        conn.execute(text(f"REVOKE SELECT ON claim_tx_history FROM {ROLE}"))
    eng = create_engine(make_url(DSN).set(username=ROLE, password="pw"))
    yield eng
    eng.dispose()
    with db.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {ROLE}"))
        conn.execute(text(f"DROP ROLE {ROLE}"))


@needs_db
def test_history_read_failure_rolls_back_and_writes_the_rest(db, no_history_select):
    ok = add_claim(db, 1)
    resigned = add_claim(db, 2, name="T-prime")
    add_history(db, resigned, "T")
    node = FakeNode({ok.tx_id: "SUCCESS", resigned.tx_id: "FAILURE"})

    # 준비 확인은 통과했다고 치고(권한이 그 뒤에 바뀐 경우) 기록 읽기가 실패하게 한다.
    logger = track(no_history_select, node, ready=True)

    assert row(db, ok.id).tx_status == TxStatus.SUCCESS
    assert row(db, resigned.id).tx_status == TxStatus.FAILURE
    assert any(
        c.args and c.args[0] == "claim_tx_history read failed"
        for c in logger.error.call_args_list
    )


@needs_db
def test_role_without_history_privilege_is_not_ready(db, no_history_select):
    history_utils.reset_history_table_ready_cache()
    assert history_utils.history_table_ready(no_history_select) is False
    assert history_utils.history_table_ready(db) is True


# --- 시험 23(추적기): 기록 테이블이 없는 DB ------------------------------------------------------------


@needs_db
def test_without_history_table_tracking_works(db, monkeypatch):
    set_config(monkeypatch, resign_mode="on")
    with db.begin() as conn:
        conn.execute(text("DROP TABLE claim_tx_history"))
    c = add_claim(db, 1)
    node = FakeNode({c.tx_id: "SUCCESS"})

    track(db, node)

    assert row(db, c.id).tx_status == TxStatus.SUCCESS
    assert history_utils.history_table_ready(db) is False


# --- 1차 검토 반영: 확정되지 않은 기록 tx 는 claim 을 확정하지 않는다 ------------------------------------------


@needs_db
@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize("current", ["INVALID", "STAGING"])
def test_unsettled_history_tx_does_not_settle_the_claim(db, monkeypatch, mode, current):
    set_config(monkeypatch, resign_mode=mode)
    c = add_claim(db, 10, name="T-prime")
    old = add_history(db, c, "T")
    node = FakeNode({c.tx_id: current, old.tx_id: "INVALID"})

    track(db, node)

    assert old.tx_id in node.asked  # 기록 tx 도 물었다
    r = row(db, c.id)
    assert (r.tx_id, r.tx, r.nonce) == (c.tx_id, c.tx, 10)  # tx 그대로
    assert r.tx_status not in (TxStatus.SUCCESS, TxStatus.FAILURE)  # 확정 안 함
    expected = (
        TxStatus.STAGED if (mode, current) == ("on", "STAGING") else TxStatus.INVALID
    )
    assert r.tx_status == expected
    assert [h for h in history(db) if h[3] == "lost"] == []  # lost 행 없음
