"""재서명(B2) — process_retry_stage 를 실제 Postgres 와 가짜 노드로 돌린다.

노드 GraphQL(tip·nonce·tx 상태·stage)과 KMS 는 가짜다. 세션·조건부 UPDATE·잠금·기록 테이블은
실제 DB 로 본다. WORKER_TEST_PG_DSN 이 없으면 건너뛴다(DB 이름에 'test' 가 없으면 시작하지 않는다).

새 이름(stage_task.get_tip_index_once 등)은 create=True 로 패치하고 config 는 __dict__ 로 바꾼다.
고치기 전 코드에서도 모듈을 읽고 각 시험이 동작으로 실패하게 하려는 것이다.
"""
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.config import config
from app.consumers import claim_consumer
from app.tasks import claim_task, stage_task
from app.utils.claim_memo import build_claim_memo
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.base import Base
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage
from shared.utils import claim_tx_history as history_utils
from shared.utils.transaction import create_grant_items_unsigned_tx, create_signed_tx
from sqlalchemy import create_engine, make_url, select, text
from sqlalchemy.orm import Session

DSN = os.environ.get("WORKER_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="WORKER_TEST_PG_DSN 이 없음")

ODIN = PlanetID.ODIN.value
HEIMDALL = PlanetID.HEIMDALL.value
SIGNER = "0x" + "12" * 20
PUBKEY = bytes.fromhex("02" + "ab" * 32)
SIGNATURE = b"\x30" * 70
AVATAR = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
REWARD = [{"ticker": "Item_NT_500000", "amount": 1, "decimal_places": 0}]
APP_NAME = "sp_b2_worker_test"

# 오딘: stage = nonce 노드(검증자), 기준은 rpc. 하임달: stage 는 rpc, nonce 는 검증자, 기준은 stage.
STAGE = {ODIN: "http://odin-validator", HEIMDALL: "http://heimdall-rpc"}
NONCE = {HEIMDALL: "http://heimdall-validator"}
REFERENCE = {ODIN: "http://odin-rpc"}


def ensure_test_database(dsn):
    name = make_url(dsn).database or ""
    if "test" not in name:
        pytest.fail(f"DB 이름 {name!r} 에 'test' 가 없어 drop_all 을 하지 않는다")


def key(planet_id):
    return planet_id.decode()


def set_config(monkeypatch, **values):
    for k, v in values.items():
        monkeypatch.setitem(config.__dict__, k, v)


@pytest.fixture(scope="module")
def engine():
    ensure_test_database(DSN)
    eng = create_engine(DSN, pool_size=10, connect_args={"application_name": APP_NAME})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(scope="module")
def observer():
    """이 시험 워커의 연결을 밖에서 보는 연결(다른 application_name)."""
    eng = create_engine(DSN)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    Base.metadata.create_all(engine)  # 테이블을 지운 시험 뒤에 되살린다
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER IF EXISTS no_tx_null ON claim"))
        conn.execute(
            text(
                "TRUNCATE claim, claim_tx_history, season_pass RESTART IDENTITY CASCADE"
            )
        )
    with Session(engine) as s:
        s.add(
            SeasonPass(
                id=1,
                pass_type=PassType.COURAGE_PASS,
                season_index=1,
                reward_list=[],
                instant_exp=0,
            )
        )
        s.commit()
    reset = getattr(history_utils, "reset_history_table_ready_cache", None)
    if reset:
        reset()
    reset = getattr(stage_task, "reset_resign_observations", None)
    if reset:
        reset()
    return engine


class Clock:
    def __init__(self):
        self.now = datetime.now(tz=timezone.utc).replace(microsecond=0)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


class Nodes:
    """가짜 노드. url 별 tip·nonce, (url, tx_id) 별 상태, stage 기록."""

    def __init__(self):
        self.tip = {}
        self.nonce = {}
        self.status = {}
        self.calls = []
        self.staged = []
        self.stage_fail = lambda planet_id, tx: False
        self.hooks = []  # 호출마다 부르는 함수(경합·세션 확인용)

    def _hook(self, kind, url):
        for hook in self.hooks:
            hook(kind, url)

    def get_tip(self, url, jwt=None, timeout=None):
        self.calls.append(("tip", url))
        self._hook("tip", url)
        value = self.tip[url]
        if isinstance(value, Exception):
            raise value
        return value

    def get_nonce(self, url, address, jwt=None, timeout=None):
        self.calls.append(("nonce", url))
        self._hook("nonce", url)
        value = self.nonce[url]
        if isinstance(value, Exception):
            raise value
        return value

    def get_status(self, url, tx_id, jwt=None, timeout=None):
        self.calls.append(("status", url, tx_id))
        self._hook("status", url)
        value = self.status.get((url, tx_id), "INVALID")
        if isinstance(value, Exception):
            raise value
        return value

    def stage(self, planet_id, signed_tx):
        self.calls.append(("stage", bytes(planet_id)))
        self._hook("stage", bytes(planet_id))
        self.staged.append((bytes(planet_id), bytes(signed_tx)))
        if self.stage_fail(bytes(planet_id), bytes(signed_tx)):
            return False, "node rejected", None
        return True, "", "txid"

    def net_calls(self):
        return [c for c in self.calls if c[0] != "stage"]

    def set_tips(self, planet_id, tip, ref_tip=None):
        nonce_url = NONCE.get(planet_id, STAGE[planet_id])
        ref_url = REFERENCE.get(planet_id, STAGE[planet_id])
        self.tip[nonce_url] = tip
        self.tip[ref_url] = tip if ref_tip is None else ref_tip

    def set_v(self, planet_id, v):
        self.nonce[NONCE.get(planet_id, STAGE[planet_id])] = v

    def staged_txs(self, planet_id):
        return [tx for p, tx in self.staged if p == planet_id]


@pytest.fixture
def world(db, monkeypatch):
    clock = Clock()
    nodes = Nodes()
    for p in (ODIN, HEIMDALL):
        nodes.set_tips(p, 1000)
    account = SimpleNamespace(
        address=SIGNER, pubkey=PUBKEY, sign_tx=MagicMock(return_value=SIGNATURE)
    )
    stage_client = MagicMock()
    stage_client.stage.side_effect = nodes.stage
    consumer_gql = MagicMock()
    consumer_gql.get_next_nonce.return_value = 0
    consumer_gql.stage.side_effect = nodes.stage
    set_config(
        monkeypatch,
        resign_mode="on",
        gql_url_map={key(p): u for p, u in STAGE.items()},
        nonce_gql_url_map={key(p): u for p, u in NONCE.items()},
        reference_gql_url_map={key(p): u for p, u in REFERENCE.items()},
        resign_after_minutes=15.0,
        chain_stall_minutes=4.0,
        validator_lag_blocks=5,
        validator_max_blocks_per_minute=16.0,
        max_resigns=3,
        resign_cap_window_hours=6.0,
        resign_batch_limit=20,
        resign_max_tx_bytes=1_000_000,
    )
    logger = MagicMock()
    patches = [
        patch.object(stage_task, "engine", db),
        patch.object(claim_consumer, "engine", db),
        patch.object(claim_task, "engine", db),
        patch.object(stage_task, "GQLClient", return_value=stage_client),
        patch.object(claim_consumer, "GQLClient", return_value=consumer_gql),
        patch.object(claim_consumer, "_get_account", return_value=account),
        patch.object(
            stage_task, "_get_resign_account", return_value=account, create=True
        ),
        patch.object(stage_task, "_utcnow", clock, create=True),
        patch.object(stage_task, "get_tip_index_once", nodes.get_tip, create=True),
        patch.object(
            stage_task, "get_next_tx_nonce_once", nodes.get_nonce, create=True
        ),
        patch.object(stage_task, "get_tx_status_once", nodes.get_status, create=True),
        patch.object(stage_task, "logger", logger),
        patch.object(claim_consumer, "logger", MagicMock()),
        patch.object(claim_task, "logger", MagicMock()),
        patch.object(stage_task.process_retry_stage, "retry"),
    ]
    for p in patches:
        p.start()
    world = SimpleNamespace(
        db=db,
        clock=clock,
        nodes=nodes,
        account=account,
        logger=logger,
        consumer_gql=consumer_gql,
        retry=stage_task.process_retry_stage.retry,
    )
    yield world
    for p in reversed(patches):
        p.stop()


def make_tx(planet_id, nonce, signed_at, normal_levels=(1,), reward=None, memo=None):
    if memo is None:
        memo = build_claim_memo(list(normal_levels), [], "CouragePass")
    unsigned = create_grant_items_unsigned_tx(
        planet_id=PlanetID(planet_id),
        public_key=PUBKEY.hex(),
        address=SIGNER,
        nonce=nonce,
        avatar_addr=AVATAR,
        claim_data=reward or REWARD,
        memo=memo,
        timestamp=signed_at,
    )
    signed = create_signed_tx(unsigned, SIGNATURE)
    return signed.hex(), hashlib.sha256(signed).hexdigest()


def add_claim(
    w,
    nonce,
    status=TxStatus.INVALID,
    age=20,
    planet_id=ODIN,
    normal_levels=(1,),
    reward=None,
    memo=None,
    with_tx=True,
):
    """age: tx 서명 시각이 지금(가짜 시계)보다 몇 분 전인지."""
    tx = tx_id = None
    if with_tx:
        tx, tx_id = make_tx(
            planet_id,
            nonce,
            w.clock() - timedelta(minutes=age),
            normal_levels,
            reward,
            memo,
        )
    with Session(w.db) as s:
        c = Claim(
            uuid=f"{key(planet_id)}-{nonce}-{time.monotonic_ns()}",
            agent_addr="0x" + "aa" * 20,
            avatar_addr=AVATAR,
            season_pass_id=1,
            planet_id=planet_id,
            normal_levels=list(normal_levels),
            premium_levels=[],
            reward_list=reward or REWARD,
            nonce=nonce,
            tx=tx,
            tx_id=tx_id,
            tx_status=status,
        )
        s.add(c)
        s.commit()
        cid = c.id
    with w.db.begin() as conn:
        conn.execute(
            text(
                "UPDATE claim SET created_at = now() - interval '1 hour' WHERE id = :i"
            ),
            {"i": cid},
        )
    return SimpleNamespace(id=cid, nonce=nonce, tx=tx, tx_id=tx_id)


def claim_row(w, cid):
    with Session(w.db) as s:
        c = s.get(Claim, cid)
        return SimpleNamespace(
            nonce=c.nonce, tx=c.tx, tx_id=c.tx_id, tx_status=c.tx_status
        )


def history_rows(w, cid=None):
    sql = "SELECT claim_id, nonce, tx_id, bound_nonce, reason FROM claim_tx_history"
    params = {}
    if cid is not None:
        sql += " WHERE claim_id = :c"
        params["c"] = cid
    with w.db.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql + " ORDER BY id"), params)]


def warm(w, planet_id, minutes=5, blocks=40):
    """직전 관측을 만든다(워커가 이미 한 회차를 돈 것처럼). 그 뒤 tip 이 blocks 만큼 오른다."""
    nonce_url = NONCE.get(planet_id, STAGE[planet_id])
    ref_url = REFERENCE.get(planet_id, STAGE[planet_id])
    v_tip, r_tip = w.nodes.tip[nonce_url], w.nodes.tip[ref_url]
    set_observation(
        planet_id,
        v_tip - blocks,
        r_tip - blocks,
        w.clock() - timedelta(minutes=minutes),
    )


def set_observation(planet_id, v_tip, r_tip, at):
    # 고치기 전 코드에는 관측 상태가 없다 — 그때는 아무것도 하지 않고 시험의 단언에서 실패하게 둔다.
    if hasattr(stage_task, "_last_obs"):
        stage_task._last_obs[planet_id] = (v_tip, r_tip, at)
        stage_task._tip_since[planet_id] = (v_tip, at)


def next_round(w, minutes=5, blocks=40):
    w.clock.advance(minutes)
    for url in list(w.nodes.tip):
        if isinstance(w.nodes.tip[url], int):
            w.nodes.tip[url] += blocks


def run(w):
    stage_task.process_retry_stage.run()
    return summaries(w)


def summaries(w):
    out = {}
    for level in ("info", "warning"):
        for c in getattr(w.logger, level).call_args_list:
            if c.args and c.args[0] == "Resign summary":
                out[c.kwargs["planet_id"]] = dict(c.kwargs, level=level)
    w.logger.reset_mock()
    return out


def forbid_tx_null(w):
    with w.db.begin() as conn:
        conn.execute(
            text(
                """
CREATE OR REPLACE FUNCTION forbid_tx_null() RETURNS trigger AS $$
BEGIN
  IF OLD.tx IS NOT NULL AND NEW.tx IS NULL THEN
    RAISE EXCEPTION 'claim % lost its tx', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql"""
            )
        )
        conn.execute(
            text(
                "CREATE TRIGGER no_tx_null BEFORE UPDATE ON claim "
                "FOR EACH ROW EXECUTE FUNCTION forbid_tx_null()"
            )
        )


# --- 시험 1: 구멍 주인을 같은 nonce 로 다시 서명 ---------------------------------------------------


def test_resigns_hole_owner_with_same_nonce(world):
    w = world
    forbid_tx_null(w)  # 어느 순간에도 tx 가 비지 않는다
    owner = add_claim(w, 10, age=20)
    fresh = add_claim(w, 11, age=1)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    row = claim_row(w, owner.id)
    assert row.nonce == 10
    assert row.tx != owner.tx and row.tx_id != owner.tx_id
    assert row.tx_status == TxStatus.STAGED
    assert history_rows(w, owner.id) == [(owner.id, 10, owner.tx_id, 10, "expired")]
    staged = w.nodes.staged_txs(ODIN)
    assert bytes.fromhex(owner.tx) not in staged  # 만료된 같은 바이트는 보내지 않는다
    assert bytes.fromhex(row.tx) in staged
    assert bytes.fromhex(fresh.tx) in staged  # 살아 있는 건은 ① 이 그대로 보낸다
    # 새 tx 의 시각은 지금이다.
    from shared.utils.transaction import tx_timestamp

    assert tx_timestamp(row.tx) == w.clock()
    assert s[key(ODIN)]["resigned"] == [10]
    assert s[key(ODIN)]["bound"] == 10
    w.retry.assert_not_called()


# --- 시험 2: N < V 는 건드리지 않는다 ----------------------------------------------------------


def test_expired_below_v_is_left_alone(world):
    w = world
    below = add_claim(w, 5, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert claim_row(w, below.id).tx_id == below.tx_id
    assert claim_row(w, below.id).tx_status == TxStatus.INVALID
    assert w.nodes.staged_txs(ODIN) == []  # on·구성됨이면 만료 건 restage 도 안 함
    assert s[key(ODIN)]["untouched"] == {"count": 1, "min": 5, "max": 5}
    assert s[key(ODIN)]["reason"] == "nothing_pending"


# --- 시험 3: 만료 전이면 지금처럼 restage ----------------------------------------------------------


def test_fresh_owner_is_restaged_as_before(world):
    w = world
    fresh = add_claim(w, 10, age=3)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert w.nodes.staged_txs(ODIN) == [bytes.fromhex(fresh.tx)]
    assert claim_row(w, fresh.id).tx_status == TxStatus.STAGED
    assert s[key(ODIN)]["reason"] == "hole_fresh"
    assert history_rows(w) == []


# --- 시험 4: off 는 지금과 같다 ---------------------------------------------------------------


def test_off_is_unchanged(world, monkeypatch):
    w = world
    set_config(monkeypatch, resign_mode="off")
    expired = add_claim(w, 10, age=20)
    fresh = add_claim(w, 11, age=1)
    other = add_claim(w, 3, age=30, planet_id=HEIMDALL)
    w.nodes.set_v(ODIN, 10)
    ready = MagicMock(return_value=True)
    with patch.object(stage_task, "history_table_ready", ready, create=True):
        s = run(w)

    assert w.nodes.staged == [
        (ODIN, bytes.fromhex(expired.tx)),
        (ODIN, bytes.fromhex(fresh.tx)),
        (HEIMDALL, bytes.fromhex(other.tx)),
    ]
    assert w.nodes.net_calls() == []
    assert history_rows(w) == []
    assert s == {}
    ready.assert_not_called()  # off 는 기록 테이블 확인도 하지 않는다
    w.account.sign_tx.assert_not_called()


# --- 시험 5: dry_run 은 지금 동작 + 로그 -------------------------------------------------------------


def test_dry_run_only_logs(world, monkeypatch):
    w = world
    set_config(monkeypatch, resign_mode="dry_run")
    owner = add_claim(w, 10, age=20)
    fresh = add_claim(w, 11, age=1)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    # restage 는 off 와 같은 입력에서 같은 호출 목록(만료 건도 보낸다)
    assert w.nodes.staged == [
        (ODIN, bytes.fromhex(owner.tx)),
        (ODIN, bytes.fromhex(fresh.tx)),
    ]
    assert claim_row(w, owner.id).tx_id == owner.tx_id
    assert history_rows(w) == []
    w.account.sign_tx.assert_not_called()
    assert s[key(ODIN)]["would"] == [10]
    assert s[key(ODIN)]["resigned"] == []


# --- 시험 6: 조회 실패는 그 플래닛만 보류 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    ["tip_exception", "tip_errors", "nonce_exception", "nonce_minus_one"],
)
def test_lookup_failure_holds_only_that_planet(world, failure):
    w = world
    odin_owner = add_claim(w, 10, age=20)
    odin_fresh = add_claim(w, 12, age=1)
    heim_owner = add_claim(w, 20, age=20, planet_id=HEIMDALL)
    w.nodes.set_v(ODIN, 10)
    w.nodes.set_v(HEIMDALL, 20)
    warm(w, ODIN)
    warm(w, HEIMDALL)
    if failure == "tip_exception":
        w.nodes.tip[STAGE[ODIN]] = ConnectionError("odin down")
    elif failure == "tip_errors":
        # get_tip_index_once 는 errors·data null 을 예외로 바꾼다(단위 시험에서 확인).
        w.nodes.tip[STAGE[ODIN]] = ValueError("errors")
    elif failure == "nonce_exception":
        w.nodes.nonce[STAGE[ODIN]] = TimeoutError("slow")
    else:
        w.nodes.nonce[STAGE[ODIN]] = -1

    s = run(w)

    assert claim_row(w, odin_owner.id).tx_id == odin_owner.tx_id
    assert s[key(ODIN)]["reason"].startswith(("tip:", "nonce:"))
    assert s[key(ODIN)]["level"] == "info"
    assert claim_row(w, heim_owner.id).tx_id != heim_owner.tx_id  # 하임달은 진행
    assert s[key(HEIMDALL)]["resigned"] == [20]
    assert bytes.fromhex(odin_fresh.tx) in w.nodes.staged_txs(ODIN)  # restage 정상
    w.retry.assert_not_called()


# --- 시험 7: 정지·첫 관측·역행 ------------------------------------------------------------------


def test_first_observation_holds(world):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)

    s = run(w)

    assert s[key(ODIN)]["reason"] == "first_observation"
    assert claim_row(w, owner.id).tx_id == owner.tx_id

    next_round(w)
    s = run(w)
    assert s[key(ODIN)]["resigned"] == [10]


def test_chain_stall_holds(world):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN, blocks=0)  # 5분 전에도 같은 tip

    s = run(w)

    assert s[key(ODIN)]["reason"] == "chain_stalled"
    assert claim_row(w, owner.id).tx_id == owner.tx_id


def test_tip_regression_holds(world):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN, blocks=-30)  # 직전 관측 tip 이 지금보다 컸다

    s = run(w)

    assert s[key(ODIN)]["reason"] == "tip_regressed"
    assert s[key(ODIN)]["level"] == "warning"
    assert claim_row(w, owner.id).tx_id == owner.tx_id


# --- 시험 8: 따라잡는 중은 시간 기준 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes,v_blocks,r_blocks,expected",
    [
        (5, 100, 100, "catching_up"),  # 분당 20 > 16
        (10, 100, 100, ""),  # beat 하나가 빠져도 분당 10 이면 진행
        (5, 30, 10, "catching_up"),  # 기준 노드보다 20 더 올라옴
    ],
)
def test_catching_up_is_rate_based(world, minutes, v_blocks, r_blocks, expected):
    w = world
    owner = add_claim(w, 10, age=30)
    w.nodes.set_v(ODIN, 10)
    nonce_url, ref_url = STAGE[ODIN], REFERENCE[ODIN]
    set_observation(
        ODIN, 1000 - v_blocks, 1000 - r_blocks, w.clock() - timedelta(minutes=minutes)
    )
    assert w.nodes.tip[nonce_url] == w.nodes.tip[ref_url] == 1000

    s = run(w)

    assert s[key(ODIN)]["reason"] == expected
    changed = claim_row(w, owner.id).tx_id != owner.tx_id
    assert changed == (expected == "")


# --- 시험 9: 뒤처짐·기준 없음 ---------------------------------------------------------------------


def test_validator_lagging_holds(world):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    w.nodes.set_tips(ODIN, 1000, ref_tip=1010)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["reason"] == "validator_lagging"
    assert claim_row(w, owner.id).tx_id == owner.tx_id


def test_no_reference_planet_restages_expired_as_before(world, monkeypatch):
    w = world
    set_config(monkeypatch, reference_gql_url_map={})  # 오딘은 stage = nonce 노드
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert w.nodes.staged_txs(ODIN) == [bytes.fromhex(owner.tx)]  # on 에서도 restage
    assert s[key(ODIN)]["reason"] == "no_reference"
    assert s[key(ODIN)]["level"] == "info"
    assert not [c for c in w.nodes.net_calls() if c[1] == STAGE[ODIN]]


# --- 시험 10: 주소 사용 -----------------------------------------------------------------------


def test_lookups_go_to_the_right_nodes(world):
    w = world
    heim = add_claim(w, 20, age=20, planet_id=HEIMDALL)
    w.nodes.set_v(HEIMDALL, 20)
    warm(w, HEIMDALL)

    run(w)

    calls = w.nodes.net_calls()
    tips = {c[1] for c in calls if c[0] == "tip"}
    assert {NONCE[HEIMDALL], STAGE[HEIMDALL]} <= tips
    nonce_urls = {c[1] for c in calls if c[0] == "nonce"}
    assert NONCE[HEIMDALL] in nonce_urls and STAGE[HEIMDALL] not in nonce_urls
    # 옛 tx 확인은 nonce·기준·stage 노드 모두에 한 번씩
    status_urls = [c[1] for c in calls if c[0] == "status" and c[2] == heim.tx_id]
    assert sorted(status_urls) == sorted({NONCE[HEIMDALL], STAGE[HEIMDALL]})
    # stage 는 언제나 stage 노드(GQLClient, 플래닛 기준)
    assert (HEIMDALL, bytes.fromhex(claim_row(w, heim.id).tx)) in w.nodes.staged


# --- 시험 11: 평상시 무소음 ----------------------------------------------------------------------


def test_nothing_pending_is_quiet(world):
    w = world
    add_claim(w, 8, status=TxStatus.SUCCESS, age=60)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["reason"] == "nothing_pending"
    assert s[key(ODIN)]["level"] == "info"
    assert not w.logger.warning.call_args_list


def test_hole_not_ours_warns_only_when_repeated(world):
    w = world
    add_claim(w, 11, age=20)  # V=10 의 주인이 없다(소각 tx 등)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == ("hole_not_ours", "info")

    next_round(w)
    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == (
        "hole_not_ours",
        "warning",
    )

    next_round(w)
    w.nodes.set_v(ODIN, 9)  # V 가 바뀌면 다시 info
    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == ("hole_not_ours", "info")
    assert history_rows(w) == []


# --- 시험 12: 구멍 주인 판정 ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "case,reason",
    [
        ("manual_row", "hole_manual_row"),
        ("settled", "hole_settled_mismatch"),
        ("fresh", "hole_fresh"),
        ("oversized", "hole_oversized"),
        ("capped", "hole_capped"),
    ],
)
def test_hole_owner_verdicts_hold_the_planet(world, monkeypatch, case, reason):
    w = world
    if case == "manual_row":
        owner = add_claim(w, 10, status=None, with_tx=False)
    elif case == "settled":
        owner = add_claim(w, 10, status=TxStatus.SUCCESS, age=20)
    elif case == "fresh":
        owner = add_claim(w, 10, age=2)
    else:
        owner = add_claim(w, 10, age=20)
    upper = add_claim(w, 11, age=20)
    if case == "oversized":
        set_config(monkeypatch, resign_max_tx_bytes=300)
    if case == "capped":
        with w.db.begin() as conn:
            for i in range(3):
                conn.execute(
                    text(
                        "INSERT INTO claim_tx_history (claim_id, planet_id, nonce, tx_id, tx,"
                        " bound_nonce, reason, created_at, updated_at)"
                        " VALUES (:c, :p, 10, :t, 'aa', 10, 'expired', now(), now())"
                    ),
                    {"c": owner.id, "p": ODIN, "t": f"old-{i}"},
                )
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["reason"] == reason
    assert claim_row(w, upper.id).tx_id == upper.tx_id  # 위쪽 만료 claim 도 손대지 않는다
    if owner.tx_id:
        assert claim_row(w, owner.id).tx_id == owner.tx_id
    assert len(history_rows(w)) == (3 if case == "capped" else 0)
    w.account.sign_tx.assert_not_called()
    if case == "oversized":
        assert s[key(ODIN)]["rebuilt_bytes"] > 300
    if case == "capped":
        assert s[key(ODIN)]["retry_after"]


# --- 시험 13: 크기는 다시 만든 tx 로 ------------------------------------------------------------------


def old_memo(normal_levels):
    """#325 이전 메모(레벨 목록을 그대로 넣었다)."""
    import json

    return json.dumps(
        {
            "season_pass": {
                "n": list(normal_levels),
                "p": [],
                "t": "claim",
                "tp": "CouragePass",
            }
        }
    )


def test_old_oversized_tx_is_resigned_when_rebuilt_small(world):
    w = world
    levels = [31] * 400_000  # 2026-11-14 인터널: 반복 보상 40만 번
    owner = add_claim(w, 10, age=20, normal_levels=levels, memo=old_memo(levels))
    assert len(owner.tx) // 2 > 1_000_000  # 옛 tx 는 1MB 초과
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    row = claim_row(w, owner.id)
    assert s[key(ODIN)]["resigned"] == [10]
    assert len(row.tx) // 2 < 1_000_000
    assert row.nonce == 10


def test_rebuilt_tx_still_oversized_holds(world, monkeypatch):
    w = world
    reward = [
        {"ticker": f"Item_NT_{500000 + i}", "amount": 1, "decimal_places": 0}
        for i in range(50)
    ]
    owner = add_claim(w, 10, age=20, reward=reward)
    set_config(monkeypatch, resign_max_tx_bytes=1000)  # 보상 목록 자체가 크다
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["reason"] == "hole_oversized"
    assert s[key(ODIN)]["rebuilt_bytes"] > 1000
    assert s[key(ODIN)]["old_bytes"] == len(owner.tx) // 2
    assert claim_row(w, owner.id).tx_id == owner.tx_id


# --- 시험 14: 상한 집계 ------------------------------------------------------------------------


def insert_history(w, claim_id, nonce, bound, reason, hours_ago=0.0, tx_id=None):
    with w.db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO claim_tx_history (claim_id, planet_id, nonce, tx_id, tx,"
                " bound_nonce, reason, created_at, updated_at) VALUES (:c, :p, :n, :t,"
                " 'aa', :b, :r, now() - make_interval(secs => :s), now())"
            ),
            {
                "c": claim_id,
                "p": ODIN,
                "n": nonce,
                "t": tx_id or f"h-{time.monotonic_ns()}",
                "b": bound,
                "r": reason,
                "s": hours_ago * 3600,
            },
        )


def test_cap_counts_only_recent_hole_resigns(world):
    w = world
    owner = add_claim(w, 10, age=20)
    insert_history(w, owner.id, 10, 10, "expired")
    insert_history(w, owner.id, 10, 10, "expired")
    insert_history(w, owner.id, 10, None, "expired")  # 동반 재서명
    insert_history(w, owner.id, 10, 10, "lost")
    insert_history(w, owner.id, 10, 10, "released")
    insert_history(w, owner.id, 10, 10, "expired", hours_ago=7)  # 창 밖
    insert_history(w, owner.id, 10, 10, "expired", hours_ago=8)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)
    assert s[key(ODIN)]["resigned"] == [10]  # 창 안의 구멍 재서명은 2+1(이번)=3

    # 이번 것까지 3번 → 다음 회차는 상한
    w.nodes.status[(STAGE[ODIN], claim_row(w, owner.id).tx_id)] = "INVALID"
    next_round(w, minutes=20)  # 새 tx 도 만료
    s = run(w)
    assert s[key(ODIN)]["reason"] == "hole_capped"

    # 원인을 고친 뒤 상한만 푼다(부록 A.3)
    with w.db.begin() as conn:
        conn.execute(
            text(
                "UPDATE claim_tx_history SET bound_nonce = NULL"
                " WHERE claim_id = :c AND reason = 'expired'"
            ),
            {"c": owner.id},
        )
    next_round(w)
    s = run(w)
    assert s[key(ODIN)]["resigned"] == [10]


# --- 시험 15·16: 동반 후보, 창 --------------------------------------------------------------------


def test_oversized_companion_is_skipped(world, monkeypatch):
    w = world
    owner = add_claim(w, 10, age=20)
    big = [
        {"ticker": f"Item_NT_{500000 + i}", "amount": 1, "decimal_places": 0}
        for i in range(30)
    ]
    add_claim(w, 11, age=20, reward=big)
    third = add_claim(w, 12, age=20)
    owner_size = len(owner.tx) // 2
    set_config(monkeypatch, resign_max_tx_bytes=owner_size + 300)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["resigned"] == [10, 12]
    assert s[key(ODIN)]["oversized"] == 1
    assert claim_row(w, third.id).tx_id != third.tx_id
    # 동반 재서명은 상한 집계에서 빠지게 bound_nonce 가 NULL 이다.
    assert history_rows(w, third.id)[0][3] is None


def test_candidates_start_at_v_even_with_many_below(world):
    w = world
    with Session(w.db) as s:
        for n in range(120):
            tx, tx_id = make_tx(ODIN, n, w.clock() - timedelta(minutes=30))
            s.add(
                Claim(
                    uuid=f"below-{n}",
                    agent_addr="0x" + "aa" * 20,
                    avatar_addr=AVATAR,
                    season_pass_id=1,
                    planet_id=ODIN,
                    normal_levels=[1],
                    premium_levels=[],
                    reward_list=REWARD,
                    nonce=n,
                    tx=tx,
                    tx_id=tx_id,
                    tx_status=TxStatus.INVALID,
                )
            )
        s.commit()
    owner = add_claim(w, 200, age=20)
    w.nodes.set_v(ODIN, 200)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["resigned"] == [200]
    assert s[key(ODIN)]["untouched"]["count"] == 120
    assert claim_row(w, owner.id).tx_id != owner.tx_id


# --- 시험 18: 조건부 교체 ------------------------------------------------------------------------


@pytest.mark.parametrize("race", ["tracker_settled", "tx_changed", "row_locked"])
def test_conditional_replace_skips_raced_rows(world, monkeypatch, race):
    w = world
    monkeypatch.setattr(stage_task, "RESIGN_LOCK_TIMEOUT", "1s", raising=False)
    owner = add_claim(w, 10, age=20)
    other = add_claim(w, 11, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    holder = {}

    def hook(kind, url):
        # 계획이 끝난 뒤(옛 tx 확인 중) 다른 경로가 그 행을 바꾼다.
        if kind != "status" or holder:
            return
        holder["done"] = True
        if race == "tracker_settled":
            with w.db.begin() as conn:
                conn.execute(
                    text("UPDATE claim SET tx_status = 'SUCCESS' WHERE id = :i"),
                    {"i": owner.id},
                )
        elif race == "tx_changed":
            with w.db.begin() as conn:
                conn.execute(
                    text("UPDATE claim SET tx_id = 'someone-else' WHERE id = :i"),
                    {"i": owner.id},
                )
        else:
            conn = w.db.connect()
            conn.execute(
                text("SELECT id FROM claim WHERE id = :i FOR UPDATE"), {"i": owner.id}
            )
            holder["conn"] = conn

    w.nodes.hooks.append(hook)
    started = time.monotonic()
    try:
        s = run(w)
    finally:
        if "conn" in holder:
            holder["conn"].rollback()
            holder["conn"].close()

    assert time.monotonic() - started < 10
    assert history_rows(w, owner.id) == []
    assert s[key(ODIN)]["raced"] == 1
    staged = w.nodes.staged_txs(ODIN)
    assert staged == [], "교체하지 못한 건의 새 tx 는 stage 하지 않는다"
    # 구멍 주인을 교체하지 못했으니 위쪽 후보도 이번 회차엔 손대지 않는다(1차 검토 4).
    assert s[key(ODIN)]["resigned"] == []
    assert s[key(ODIN)]["owner_result"] == "raced"
    assert claim_row(w, other.id).tx_id == other.tx_id


# --- 시험 19: 교체 뒤 stage 실패 -------------------------------------------------------------------


def test_stage_failure_after_replace_keeps_new_tx_for_restage(world):
    w = world
    owner = add_claim(w, 10, age=20)
    upper = add_claim(w, 11, age=20)
    old = {bytes.fromhex(owner.tx), bytes.fromhex(upper.tx)}
    w.nodes.stage_fail = lambda p, tx: tx not in old  # 새 tx 는 거부
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    row = claim_row(w, owner.id)
    assert (row.nonce, row.tx_status) == (10, TxStatus.CREATED)
    assert row.tx_id != owner.tx_id
    assert history_rows(w, owner.id) == [(owner.id, 10, owner.tx_id, 10, "expired")]
    assert claim_row(w, upper.id).tx_id == upper.tx_id  # 남은 후보는 이번에 건너뜀
    assert s[key(ODIN)]["stage_failed"] is True
    assert s[key(ODIN)]["level"] == "warning"

    # 다음 회차 ① 이 새 tx 를 보낸다(새 tx 는 아직 만료 전).
    w.nodes.stage_fail = lambda p, tx: False
    w.nodes.staged.clear()
    w.clock.advance(5)
    with w.db.begin() as conn:
        conn.execute(text("UPDATE claim SET created_at = now() - interval '1 hour'"))
    run(w)
    assert bytes.fromhex(row.tx) in w.nodes.staged_txs(ODIN)
    assert claim_row(w, owner.id).tx_status == TxStatus.STAGED


# --- 시험 20: 옛 tx 사전 확인 ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    ["SUCCESS", "FAILURE", "INCLUDED", "STAGING", ConnectionError("down"), "INVALID"],
)
@pytest.mark.parametrize("where", ["nonce", "reference"])
def test_old_tx_precheck(world, status, where):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    url = STAGE[ODIN] if where == "nonce" else REFERENCE[ODIN]
    w.nodes.status[(url, owner.tx_id)] = status

    s = run(w)

    replaced = claim_row(w, owner.id).tx_id != owner.tx_id
    assert replaced == (status == "INVALID")
    if not replaced:
        assert s[key(ODIN)]["settled"] == 1
    checks = [c[1] for c in w.nodes.calls if c[0] == "status" and c[2] == owner.tx_id]
    assert len(checks) == len(set(checks))  # 노드당 한 번만


# --- 시험 21: 네트워크 호출 중 열린 트랜잭션이 없다 ---------------------------------------------------------


def test_no_idle_in_transaction_during_network_calls(world, observer):
    w = world
    # 오딘: 첫 restage 실패(break), 하임달: 정상 + 재서명, 그리고 조회 결과가 빈 경우는 오딘 restage 뒤 하임달 판단.
    odin_first = add_claim(w, 3, age=1)
    add_claim(w, 4, age=1)
    heim_owner = add_claim(w, 20, age=20, planet_id=HEIMDALL)
    add_claim(w, 21, age=1, planet_id=HEIMDALL)
    w.nodes.stage_fail = lambda p, tx: tx == bytes.fromhex(odin_first.tx)
    w.nodes.set_v(ODIN, 50)  # 오딘은 기다리는 건 없음(빈 결과)
    w.nodes.set_v(HEIMDALL, 20)
    warm(w, ODIN)
    warm(w, HEIMDALL)
    seen = []

    def hook(kind, url):
        with observer.connect() as conn:
            n = conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE application_name = :a"
                    " AND state LIKE 'idle in transaction%'"
                ),
                {"a": APP_NAME},
            )
        seen.append((kind, url, n))

    w.nodes.hooks.append(hook)

    s = run(w)

    assert s[key(HEIMDALL)]["resigned"] == [20]
    assert claim_row(w, heim_owner.id).tx_id != heim_owner.tx_id
    assert seen and all(n == 0 for _, _, n in seen), seen


def test_no_idle_in_transaction_after_empty_restage(world, observer):
    w = world
    heim = add_claim(w, 21, age=1, planet_id=HEIMDALL)  # 오딘 restage 결과는 비어 있다
    seen = []

    def hook(kind, url):
        with observer.connect() as conn:
            seen.append(
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name = :a"
                        " AND state LIKE 'idle in transaction%'"
                    ),
                    {"a": APP_NAME},
                )
            )

    w.nodes.hooks.append(hook)
    run(w)
    assert bytes.fromhex(heim.tx) in w.nodes.staged_txs(HEIMDALL)
    assert seen and all(n == 0 for n in seen), seen


# --- 시험 22: 시간 상한 ------------------------------------------------------------------------


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t


def test_time_budget_round_robin(world, monkeypatch):
    w = world
    fake = FakeTime()
    monkeypatch.setattr(stage_task, "time", fake)
    for n in (10, 11, 12):
        add_claim(w, n, age=20)
        add_claim(w, n + 10, age=20, planet_id=HEIMDALL)
    w.nodes.set_v(ODIN, 10)
    w.nodes.set_v(HEIMDALL, 20)
    warm(w, ODIN)
    warm(w, HEIMDALL)

    def slow_stage(kind, url):
        if kind == "stage":
            fake.t += 50  # 느린 노드

    w.nodes.hooks.append(slow_stage)

    s = run(w)

    # 0초 오딘 10 → 50초 하임달 20 → 100초 오딘 11 → 150초: 새 후보를 시작하지 않는다
    assert s[key(ODIN)]["resigned"] == [10, 11]
    assert s[key(HEIMDALL)]["resigned"] == [20]
    assert s[key(HEIMDALL)]["out_of_time"] == 2
    assert s[key(ODIN)]["out_of_time"] == 1
    assert s[key(HEIMDALL)]["level"] == "warning"


def test_time_budget_stops_new_planet_judgement(world, monkeypatch):
    w = world
    fake = FakeTime()
    monkeypatch.setattr(stage_task, "time", fake)
    add_claim(w, 3, age=1)  # ① restage 가 오래 걸린다
    add_claim(w, 20, age=20, planet_id=HEIMDALL)
    w.nodes.set_v(HEIMDALL, 20)
    warm(w, HEIMDALL)

    def slow_restage(kind, url):
        if kind == "stage":
            fake.t += 130

    w.nodes.hooks.append(slow_restage)
    s = run(w)

    # ② 판단(tip·nonce)은 ① 보다 먼저 끝났고, ① 이 상한을 넘긴 뒤에는 새 조회(V 다시 읽기·옛 tx 확인)를
    # 시작하지 않았다.
    first_stage = next(i for i, c in enumerate(w.nodes.calls) if c[0] == "stage")
    assert w.nodes.calls[first_stage:] == [("stage", ODIN)]
    assert s[key(HEIMDALL)]["reason"] == "out_of_time"


def test_batch_limit_per_planet(world, monkeypatch):
    w = world
    set_config(monkeypatch, resign_batch_limit=2)
    for n in (10, 11, 12):
        add_claim(w, n, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    s = run(w)

    assert s[key(ODIN)]["resigned"] == [10, 11]


# --- 시험 23: 기록 테이블이 없는 DB(마이그레이션 전) ----------------------------------------------------


def test_without_history_table_everything_else_works(world):
    w = world
    with w.db.begin() as conn:
        conn.execute(text("DROP TABLE claim_tx_history"))
    expired = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    # 새 claim(재처리 대상)
    with Session(w.db) as s:
        s.add(
            Claim(
                uuid="new-1",
                agent_addr="0x" + "aa" * 20,
                avatar_addr=AVATAR,
                season_pass_id=1,
                planet_id=HEIMDALL,
                normal_levels=[1],
                premium_levels=[],
                reward_list=REWARD,
            )
        )
        s.commit()
    with w.db.begin() as conn:
        conn.execute(text("UPDATE claim SET created_at = now() - interval '1 hour'"))

    stage_task.process_retry_stage.run()
    warnings = [
        c
        for c in w.logger.warning.call_args_list
        if c.args and c.args[0] == "claim_tx_history not ready; resign disabled"
    ]
    claim_task.process_retry_claim.run()

    assert len(warnings) == 1
    assert w.nodes.staged_txs(ODIN) == [bytes.fromhex(expired.tx)]  # 지금처럼 restage
    assert claim_row(w, expired.id).tx_id == expired.tx_id
    with Session(w.db) as s:
        new = s.scalar(select(Claim).where(Claim.uuid == "new-1"))
        assert (new.nonce, new.tx_status) == (0, TxStatus.STAGED)
    w.retry.assert_not_called()


# --- 시험 24: 권한 없는 역할 ------------------------------------------------------------------------


ROLE = "sp_b2_test_noacl"


@pytest.fixture
def limited_role(db):
    with db.begin() as conn:
        conn.execute(
            text(f"DROP OWNED BY {ROLE}") if role_exists(conn) else text("SELECT 1")
        )
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD 'pw'"))
        conn.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {ROLE}"))
        conn.execute(text(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {ROLE}"))
    url = make_url(DSN).set(username=ROLE, password="pw")
    eng = create_engine(url, connect_args={"application_name": APP_NAME})
    yield eng
    eng.dispose()
    with db.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {ROLE}"))
        conn.execute(text(f"DROP ROLE {ROLE}"))


def role_exists(conn):
    return conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROLE})


@pytest.mark.parametrize(
    "revoke",
    [
        "REVOKE INSERT ON claim_tx_history FROM {role}",
        "REVOKE USAGE, SELECT, UPDATE ON SEQUENCE claim_tx_history_id_seq FROM {role}",
    ],
)
def test_role_without_history_privilege(world, limited_role, revoke):
    w = world
    with w.db.begin() as conn:
        conn.execute(text(revoke.format(role=ROLE)))
    expired = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    with patch.object(stage_task, "engine", limited_role), patch.object(
        claim_consumer, "engine", limited_role
    ):
        assert history_utils.history_table_ready(limited_role) is False
        stage_task.process_retry_stage.run()
        add_claim(w, None, status=None, with_tx=False, planet_id=HEIMDALL)
        with Session(w.db) as s:
            uuid = s.scalar(select(Claim.uuid).where(Claim.tx.is_(None)))
        claim_consumer.consume_claim_message(ClaimMessage(uuid=uuid))

    assert claim_row(w, expired.id).tx_id == expired.tx_id  # 재서명 꺼짐
    assert w.nodes.staged_txs(ODIN) == [bytes.fromhex(expired.tx)]  # restage 는 지금처럼
    with Session(w.db) as s:
        paid = s.scalar(select(Claim).where(Claim.uuid == uuid))
        assert paid.tx_status == TxStatus.STAGED  # 지급은 정상
    assert history_rows(w) == []


def test_role_with_all_privileges_is_ready(limited_role):
    history_utils.reset_history_table_ready_cache()
    assert history_utils.history_table_ready(limited_role) is True


# --- 시험 25: restage STAGED 조건부 ------------------------------------------------------------------


def test_restage_does_not_overwrite_tracker_success(world):
    w = world
    c = add_claim(w, 10, age=1)

    def tracker_settles(kind, url):
        if kind == "stage":
            with w.db.begin() as conn:
                conn.execute(
                    text("UPDATE claim SET tx_status = 'SUCCESS' WHERE id = :i"),
                    {"i": c.id},
                )

    w.nodes.hooks.append(tracker_settles)
    run(w)

    assert claim_row(w, c.id).tx_status == TxStatus.SUCCESS


# --- consumer: 시험 28~31 ---------------------------------------------------------------------


def blank_claim(w, status=None, planet_id=ODIN):
    """tx·nonce 가 빈 claim. status 가 있으면 사람이 nonce 를 비운 행."""
    with Session(w.db) as s:
        c = Claim(
            uuid=f"blank-{time.monotonic_ns()}",
            agent_addr="0x" + "aa" * 20,
            avatar_addr=AVATAR,
            season_pass_id=1,
            planet_id=planet_id,
            normal_levels=[1],
            premium_levels=[],
            reward_list=REWARD,
            tx_status=status,
        )
        s.add(c)
        s.commit()
        return SimpleNamespace(id=c.id, uuid=c.uuid)


def consume(w, uuid):
    w.consumer_gql.get_next_nonce.return_value = 0
    claim_consumer.consume_claim_message(ClaimMessage(uuid=uuid))


@pytest.mark.parametrize("mode", ["off", "dry_run", "on"])
@pytest.mark.parametrize("reason", ["expired", "lost"])
def test_history_blocks_new_nonce_in_every_mode(world, monkeypatch, mode, reason):
    w = world
    set_config(monkeypatch, resign_mode=mode)
    c = blank_claim(w)
    insert_history(w, c.id, 10, None, reason)

    consume(w, c.uuid)

    row = claim_row(w, c.id)
    assert (row.nonce, row.tx) == (None, None)
    w.account.sign_tx.assert_not_called()
    w.consumer_gql.get_next_nonce.assert_not_called()


@pytest.mark.parametrize(
    "mode,status,blocked",
    [
        ("on", TxStatus.INVALID, True),
        ("dry_run", TxStatus.INVALID, False),
        ("off", TxStatus.INVALID, False),
        ("on", None, False),
        ("dry_run", None, False),
        ("off", None, False),
    ],
)
def test_status_based_block_only_in_on(world, monkeypatch, mode, status, blocked):
    w = world
    set_config(monkeypatch, resign_mode=mode)
    c = blank_claim(w, status=status)
    with patch.object(claim_consumer, "logger") as logger:
        consume(w, c.uuid)

    row = claim_row(w, c.id)
    if blocked:
        assert (row.nonce, row.tx) == (None, None)
        assert logger.error.call_args.kwargs["basis"] == "status"
    else:
        assert row.nonce == 0 and row.tx_status == TxStatus.STAGED
    warned = [
        call
        for call in logger.warning.call_args_list
        if call.args and call.args[0] == "Would refuse nonce reset"
    ]
    assert bool(warned) == (mode == "dry_run" and status is not None)


def test_history_check_failure_rolls_back_savepoint_only(world, monkeypatch):
    from app.utils import nonce_lock

    w = world
    monkeypatch.setattr(nonce_lock, "LOCK_TIMEOUT", "1s")
    c = blank_claim(w)
    assert history_utils.history_table_ready(w.db)  # 미리 확인해 둔다(캐시)
    holder = w.db.connect()
    holder.execute(text("LOCK TABLE claim_tx_history IN ACCESS EXCLUSIVE MODE"))
    try:
        with patch.object(claim_consumer, "logger") as logger:
            consume(w, c.uuid)
    finally:
        holder.rollback()
        holder.close()

    row = claim_row(w, c.id)
    assert row.nonce == 0 and row.tx_status == TxStatus.STAGED  # 같은 세션에서 지급을 마쳤다
    errors = [
        call
        for call in logger.error.call_args_list
        if call.args and call.args[0] == "claim_tx_history check failed; continuing"
    ]
    assert len(errors) == 1


def test_released_history_does_not_block(world):
    w = world
    c = blank_claim(w)
    insert_history(w, c.id, 10, None, "released")

    consume(w, c.uuid)

    assert claim_row(w, c.id).tx_status == TxStatus.STAGED


def test_refuses_database_without_test_in_name():
    with pytest.raises(pytest.fail.Exception):
        ensure_test_database("postgresql://u:p@db:5432/season_pass")


# --- 1차 검토 반영: 재서명이 맡을 수 있을 때만 만료 건 restage 를 건너뛴다 ------------------------------------


@pytest.mark.parametrize("case", ["bound", "reference_down", "nonce_down"])
def test_expired_restage_is_skipped_only_when_resign_takes_the_planet(world, case):
    w = world
    owner = add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    if case == "reference_down":
        w.nodes.tip[REFERENCE[ODIN]] = ConnectionError("rpc down")
    elif case == "nonce_down":
        w.nodes.nonce[STAGE[ODIN]] = TimeoutError("slow")

    s = run(w)

    if case == "bound":
        # 이번 회차에 재서명이 맡는다: 만료된 같은 바이트는 보내지 않고 다시 서명한다.
        assert bytes.fromhex(owner.tx) not in w.nodes.staged_txs(ODIN)
        assert s[key(ODIN)]["restage_skipped_expired"] == 1
        assert s[key(ODIN)]["resigned"] == [10]
    else:
        # 재서명이 맡지 못하면 예전처럼 restage 한다(조용히 멈추지 않는다).
        assert w.nodes.staged_txs(ODIN) == [bytes.fromhex(owner.tx)]
        assert claim_row(w, owner.id).tx_status == TxStatus.STAGED
        assert claim_row(w, owner.id).tx_id == owner.tx_id
        assert s[key(ODIN)]["restage_skipped_expired"] == 0
        assert s[key(ODIN)]["reason"].startswith(("tip:", "nonce:"))
        assert history_rows(w) == []


def test_repeated_lookup_failure_warns(world):
    w = world
    add_claim(w, 10, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    w.nodes.tip[REFERENCE[ODIN]] = ConnectionError("rpc down")

    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == (
        "tip:ConnectionError",
        "info",
    )

    next_round(w)
    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == (
        "tip:ConnectionError",
        "warning",
    )

    next_round(w)
    w.nodes.tip[REFERENCE[ODIN]] = w.nodes.tip[STAGE[ODIN]]  # 기준 노드 회복
    s = run(w)
    assert s[key(ODIN)]["reason"] == ""
    assert s[key(ODIN)]["resigned"] == [10]

    next_round(w)
    w.nodes.nonce[STAGE[ODIN]] = TimeoutError("slow")  # 연속이 끊겼으니 다시 info 부터
    s = run(w)
    assert (s[key(ODIN)]["reason"], s[key(ODIN)]["level"]) == (
        "nonce:TimeoutError",
        "info",
    )


# --- 1차 검토 반영: 구멍 주인을 재서명하지 못하면 위쪽 후보도 멈춘다 ------------------------------------------


@pytest.mark.parametrize("race", ["settled", "raced"])
def test_upper_candidates_wait_when_owner_is_not_resigned(world, race):
    w = world
    owner = add_claim(w, 10, age=20)
    upper = add_claim(w, 11, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    if race == "settled":
        w.nodes.status[(REFERENCE[ODIN], owner.tx_id)] = "STAGING"  # 옛 tx 가 살아 있다
    else:

        def hook(kind, url):
            if kind == "status" and not getattr(hook, "done", False):
                hook.done = True
                with w.db.begin() as conn:
                    conn.execute(
                        text("UPDATE claim SET tx_id = 'someone-else' WHERE id = :i"),
                        {"i": owner.id},
                    )

        w.nodes.hooks.append(hook)

    s = run(w)

    assert s[key(ODIN)]["resigned"] == []
    assert s[key(ODIN)]["owner_result"] == race
    assert claim_row(w, upper.id).tx_id == upper.tx_id
    assert history_rows(w) == []
    w.account.sign_tx.assert_called_once() if race == "raced" else (
        w.account.sign_tx.assert_not_called()
    )
    assert not [c for c in w.nodes.calls if c[0] == "status" and c[2] == upper.tx_id]


# --- 1차 검토 반영: 첫 후보는 언제나 구멍 주인 ---------------------------------------------------------


def test_owner_settled_between_owner_and_candidate_reads_holds(world):
    from sqlalchemy import event

    w = world
    owner = add_claim(w, 10, age=20)
    upper = add_claim(w, 11, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)
    fired = []

    def after(conn, cursor, statement, parameters, context, executemany):
        # 주인 확인 직후(상한 집계 읽기)에 추적기가 주인을 확정한다 — 후보 읽기에는 주인이 없다.
        if (
            fired
            or "FROM claim_tx_history" not in statement
            or "count(" not in statement
        ):
            return
        fired.append(True)
        with w.db.begin() as other:
            other.execute(
                text("UPDATE claim SET tx_status = 'SUCCESS' WHERE id = :i"),
                {"i": owner.id},
            )

    event.listen(w.db, "after_cursor_execute", after)
    try:
        s = run(w)
    finally:
        event.remove(w.db, "after_cursor_execute", after)

    assert fired
    assert s[key(ODIN)]["reason"] == "hole_owner_changed"
    assert claim_row(w, upper.id).tx_id == upper.tx_id  # 구멍 위를 먼저 재서명하지 않는다
    assert history_rows(w) == []
    w.account.sign_tx.assert_not_called()


def test_bound_is_reread_after_restage(world):
    # ② 는 ① 앞에서 돈다. ① 이 살아 있는 구멍 주인을 다시 보내 V 가 오르면, 계획은 오른 V 로 한다.
    w = world
    fresh = add_claim(w, 10, age=3)
    expired = add_claim(w, 11, age=20)
    w.nodes.set_v(ODIN, 10)
    warm(w, ODIN)

    def node_takes_restaged(kind, url):
        if kind == "stage":
            w.nodes.set_v(ODIN, 11)

    w.nodes.hooks.append(node_takes_restaged)

    s = run(w)

    assert claim_row(w, fresh.id).tx_status == TxStatus.STAGED
    assert s[key(ODIN)]["bound"] == 11
    assert s[key(ODIN)]["bound_before_restage"] == 10
    assert s[key(ODIN)]["resigned"] == [11]
    assert claim_row(w, expired.id).tx_id != expired.tx_id
