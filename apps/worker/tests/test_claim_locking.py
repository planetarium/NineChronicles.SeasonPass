"""같은 claim·같은 플래닛 nonce 를 두 워커가 동시에 처리하지 못하게 하는 잠금.

실제 Postgres 가 있어야 의미가 있는 시험이다(행 잠금·advisory lock). WORKER_TEST_PG_DSN 이
없으면 건너뛴다. 로컬: createdb season_pass_test_c 후
WORKER_TEST_PG_DSN=postgresql://postgres:postgres@localhost:5432/season_pass_test_c

KMS(Account)·노드 GQL 은 가짜로 바꾼다. 첫 서명 호출만 `release` 가 열릴 때까지 붙잡아,
앞선 처리가 잠금을 쥔 채 KMS 를 기다리는 동안 두 번째 처리가 어떻게 되는지 본다.
"""
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.consumers import claim_consumer
from app.tasks import burn_asset_task
from app.utils.nonce_lock import planet_nonce_lock_key
from shared.enums import PassType, PlanetID, TxStatus
from shared.models.base import Base
from shared.models.season_pass import SeasonPass
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

DSN = os.environ.get("WORKER_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="WORKER_TEST_PG_DSN 이 없음")

ODIN = PlanetID.ODIN.value
HEIMDALL = PlanetID.HEIMDALL.value
CHAIN_NONCE = 5
WAIT = 0.5


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DSN, pool_size=10)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db(engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE claim, season_pass RESTART IDENTITY CASCADE"))
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
    return engine


def add_claim(engine, uuid, planet_id=ODIN, nonce=None):
    with Session(engine) as s:
        s.add(
            Claim(
                uuid=uuid,
                agent_addr="0x" + "aa" * 20,
                avatar_addr="0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa",
                season_pass_id=1,
                planet_id=planet_id,
                normal_levels=[1],
                premium_levels=[],
                reward_list=[
                    {"ticker": "Item_NT_500000", "amount": 1, "decimal_places": 0}
                ],
                nonce=nonce,
            )
        )
        s.commit()


def read_claims(engine):
    with Session(engine) as s:
        return {c.uuid: c for c in s.scalars(select(Claim))}


def try_lock(engine, planet_id):
    """다른 연결에서 그 플래닛 잠금을 바로 잡을 수 있는지(잡았다면 곧바로 놓는다)."""
    with engine.connect() as conn:
        got = conn.scalar(
            text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": planet_nonce_lock_key(planet_id)},
        )
        conn.rollback()
        return got


class Gate:
    """첫 서명 호출만 release 가 열릴 때까지 붙잡는다."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def sign(self, unsigned_tx):
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            assert self.release.wait(10), "시험이 release 를 열지 않음"
        return b"\x30" * 70


@pytest.fixture
def worker(db):
    gate = Gate()
    account = SimpleNamespace(
        address="0x" + "12" * 20,
        pubkey=bytes.fromhex("02" + "ab" * 32),
        sign_tx=MagicMock(side_effect=gate.sign),
    )
    gql = MagicMock()
    gql.get_next_nonce.return_value = CHAIN_NONCE
    gql.stage.return_value = (True, "", None)
    with patch.object(claim_consumer, "engine", db), patch.object(
        claim_consumer, "_get_account", return_value=account
    ), patch.object(claim_consumer, "GQLClient", return_value=gql), patch.object(
        claim_consumer, "logger"
    ) as logger:
        yield SimpleNamespace(gate=gate, account=account, gql=gql, logger=logger)
        gate.release.set()


def start(uuid):
    result = {}

    def target():
        try:
            claim_consumer.consume_claim_message(ClaimMessage(uuid=uuid))
        except Exception as e:  # 시험에서 확인한다
            result["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, result


def outcomes(logger):
    return sorted(
        c.kwargs["outcome"]
        for c in logger.info.call_args_list
        if c.args and c.args[0] == "claim_timing"
    )


def test_same_claim_twice_second_skips(worker, db):
    # API 메시지와 beat 재처리가 같은 claim 을 동시에 잡은 경우.
    add_claim(db, "c1")

    t1, r1 = start("c1")
    assert worker.gate.entered.wait(5)
    t2, r2 = start("c1")
    time.sleep(WAIT)
    assert t2.is_alive()  # 행 잠금에서 앞의 commit 을 기다린다
    worker.gate.release.set()
    t1.join(10)
    t2.join(10)

    assert not r1 and not r2
    assert worker.gate.calls == 1  # 서명은 한 번만
    assert worker.gql.stage.call_count == 1
    assert outcomes(worker.logger) == ["skip_tx_exists", "staged"]
    c = read_claims(db)["c1"]
    assert (c.nonce, c.tx_status) == (CHAIN_NONCE, TxStatus.STAGED)


def test_two_claims_same_planet_get_distinct_nonces(worker, db):
    add_claim(db, "c1")
    add_claim(db, "c2")

    t1, r1 = start("c1")
    assert worker.gate.entered.wait(5)
    t2, r2 = start("c2")
    time.sleep(WAIT)
    # 두 번째는 플래닛 잠금에서 기다려 nonce 를 아직 계산하지 않는다.
    assert worker.gql.get_next_nonce.call_count == 1
    worker.gate.release.set()
    t1.join(10)
    t2.join(10)

    assert not r1 and not r2
    claims = read_claims(db)
    assert sorted(c.nonce for c in claims.values()) == [CHAIN_NONCE, CHAIN_NONCE + 1]
    assert all(c.tx_status == TxStatus.STAGED for c in claims.values())


def test_other_planet_is_not_blocked(worker, db):
    add_claim(db, "odin", planet_id=ODIN)
    add_claim(db, "heimdall", planet_id=HEIMDALL)

    t1, r1 = start("odin")
    assert worker.gate.entered.wait(5)
    t2, r2 = start("heimdall")
    t2.join(5)
    assert not t2.is_alive() and not r2  # 오딘 잠금과 무관하게 끝난다
    worker.gate.release.set()
    t1.join(10)

    assert not r1
    assert {c.uuid: c.nonce for c in read_claims(db).values()} == {
        "odin": CHAIN_NONCE,
        "heimdall": CHAIN_NONCE,
    }


def test_lock_held_while_signing_and_released_on_commit(worker, db):
    add_claim(db, "c1")
    held = []

    def sign(unsigned_tx):
        held.append(try_lock(db, ODIN))
        return b"\x30" * 70

    worker.account.sign_tx.side_effect = sign
    claim_consumer.consume_claim_message(ClaimMessage(uuid="c1"))

    assert held == [False]
    assert try_lock(db, ODIN)


def test_locks_released_on_failure(worker, db):
    add_claim(db, "c1")
    worker.account.sign_tx.side_effect = RuntimeError("KMS down")

    with pytest.raises(RuntimeError):
        claim_consumer.consume_claim_message(ClaimMessage(uuid="c1"))

    assert try_lock(db, ODIN)
    with db.connect() as conn:  # 행 잠금도 풀렸다(NOWAIT 이 바로 성공)
        conn.execute(text("SELECT id FROM claim FOR UPDATE NOWAIT"))
        conn.rollback()
    c = read_claims(db)["c1"]
    assert (c.nonce, c.tx) == (None, None)


def test_burn_asset_waits_for_planet_lock_and_holds_it_until_stage(db):
    gql = MagicMock()
    gql.get_next_nonce.return_value = CHAIN_NONCE
    held_at_stage = []

    def stage(planet_id, signed_tx):
        held_at_stage.append(try_lock(db, ODIN))
        return True, "", None

    gql.stage.side_effect = stage
    account = MagicMock()
    account.address = "0x" + "12" * 20
    account.pubkey = bytes.fromhex("02" + "ab" * 32)
    account.sign_tx.return_value = b"\x30" * 70

    holder = db.connect()
    holder.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": planet_nonce_lock_key(ODIN)}
    )
    result = {}
    with patch.object(burn_asset_task, "engine", db), patch.object(
        burn_asset_task, "Account", return_value=account
    ), patch.object(burn_asset_task, "GQLClient", return_value=gql), patch.object(
        burn_asset_task, "logger"
    ):
        t = threading.Thread(
            target=lambda: result.setdefault(
                "r",
                burn_asset_task.process_burn_asset(
                    {"ticker": "NCG", "amount": "1", "planet_id": ODIN.decode()}
                ),
            ),
            daemon=True,
        )
        t.start()
        time.sleep(WAIT)
        gql.get_next_nonce.assert_not_called()  # claim 쪽이 쥔 잠금을 기다린다
        holder.rollback()
        holder.close()
        t.join(10)

    assert result["r"].startswith("Burn asset transaction processed")
    assert held_at_stage == [False]  # stage 까지 잠금을 쥔다
    assert try_lock(db, ODIN)  # 끝나면 풀린다
