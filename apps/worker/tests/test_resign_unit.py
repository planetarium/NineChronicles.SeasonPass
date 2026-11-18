"""재서명(B2) 단위 시험 — DB 없이 확인할 수 있는 것.

새 이름(stage_task.replace_tx_stmt 등)은 모듈 속성으로 부른다. 고치기 전 코드에서는 모듈을 읽는 데는
성공하고 각 시험이 따로 실패하게 하려는 것이다.
"""
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.config import config
from app.consumers import claim_consumer
from app.tasks import stage_task
from app.utils import gql as gql_utils
from app.utils import nonce_lock
from app.utils.claim_memo import build_claim_memo
from shared.enums import PlanetID
from shared.schemas.message import ClaimMessage
from shared.utils import actions
from shared.utils.transaction import create_grant_items_unsigned_tx, create_signed_tx
from sqlalchemy.dialects import postgresql

ODIN = PlanetID.ODIN.value
HEIMDALL = PlanetID.HEIMDALL.value
SIGNER = "0x" + "12" * 20
PUBKEY = bytes.fromhex("02" + "ab" * 32)
SIGNATURE = b"\x30" * 70
AVATAR = "0x8bA11bEf1DB41F3118f7478cCfcbE7f1Af4650fa"
REWARD = [{"ticker": "Item_NT_500000", "amount": 1, "decimal_places": 0}]
FIXED = datetime(2026, 11, 18, 3, 4, 5, 678901, tzinfo=timezone.utc)


def set_config(monkeypatch, **values):
    """config 값을 바꾼다. 고치기 전 config 에 없는 필드도 넣을 수 있게 __dict__ 로 넣는다."""
    for key, value in values.items():
        monkeypatch.setitem(config.__dict__, key, value)


# --- 시험 10: 주소 선택 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nonce_map,ref_map,expected",
    [
        # 아무것도 없으면 nonce = stage, 기준은 stage 와 같아 없음 → 재서명 안 함
        ({}, {}, ("http://stage", "http://stage", None)),
        # 하임달 메인넷: nonce 는 검증자, 기준은 stage(rpc)
        (
            {"0x000000000001": "http://validator"},
            {},
            ("http://stage", "http://validator", "http://stage"),
        ),
        # 오딘 메인넷: stage = 검증자, 기준은 따로 넣은 rpc
        (
            {},
            {"0x000000000001": "http://rpc"},
            ("http://stage", "http://stage", "http://rpc"),
        ),
        # 기준이 nonce 노드와 같으면 없음
        (
            {"0x000000000001": "http://validator"},
            {"0x000000000001": "http://validator"},
            ("http://stage", "http://validator", None),
        ),
    ],
)
def test_address_selection(monkeypatch, nonce_map, ref_map, expected):
    set_config(
        monkeypatch,
        gql_url_map={"0x000000000001": "http://stage"},
        nonce_gql_url_map=nonce_map,
        reference_gql_url_map=ref_map,
    )
    got = (
        stage_task.stage_url(HEIMDALL),
        stage_task.nonce_url(HEIMDALL),
        stage_task.reference_url(HEIMDALL),
    )
    assert got == expected
    assert stage_task.resign_configured(HEIMDALL) == (expected[2] is not None)


# --- 시험 17: 교체 UPDATE 에 nonce 가 없다 ----------------------------------------------------------


def test_replace_statement_never_sets_nonce():
    target = SimpleNamespace(id=7, tx_id="old", nonce=10)
    stmt = stage_task.replace_tx_stmt(target, "beef", "new")
    compiled = stmt.compile(dialect=postgresql.dialect())
    set_clause = str(compiled).split(" WHERE ")[0]
    assert "nonce" not in set_clause
    assert set(compiled.params) >= {"id_1", "tx_id_1", "nonce_1"}
    assert compiled.params["nonce_1"] == 10 and compiled.params["tx_id_1"] == "old"
    where = str(compiled).split(" WHERE ")[1]
    assert "claim.tx_status IN" in where


def test_resign_lock_timeout_is_five_seconds():
    assert stage_task.RESIGN_LOCK_TIMEOUT == "5s"
    assert stage_task.RETRY_STAGE_BUDGET_SEC == 120


# --- set_lock_timeout 인자 ---------------------------------------------------------------------


def test_set_lock_timeout_takes_value_and_reads_default_at_call(monkeypatch):
    sess = MagicMock()
    nonce_lock.set_lock_timeout(sess, "5s")
    monkeypatch.setattr(nonce_lock, "LOCK_TIMEOUT", "1s")
    nonce_lock.set_lock_timeout(sess)
    sql = [str(c.args[0]) for c in sess.execute.call_args_list]
    assert sql == ["SET LOCAL lock_timeout = '5s'", "SET LOCAL lock_timeout = '1s'"]
    with pytest.raises(ValueError):
        nonce_lock.set_lock_timeout(sess, "5s'; DROP TABLE claim; --")


# --- 판단용 단발 조회 ----------------------------------------------------------------------------


def fake_response(body, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = body
    resp.raise_for_status.side_effect = None if status == 200 else Exception("http")
    return resp


@pytest.mark.parametrize(
    "body",
    [
        {"data": None, "errors": [{"message": "boom"}]},
        {"errors": [{"message": "boom"}]},
        {"data": None},
        {},
    ],
)
def test_single_shot_queries_raise_on_errors_or_missing_data(body):
    error = getattr(gql_utils, "GQLQueryError", None)
    assert error is not None, "errors·data 누락을 알리는 예외가 없다"
    with patch.object(gql_utils.requests, "post", return_value=fake_response(body)):
        with pytest.raises(error):
            gql_utils.get_tip_index_once("http://n", None, 5)
        with pytest.raises(error):
            gql_utils.get_next_tx_nonce_once("http://n", SIGNER, None, 5)
        with pytest.raises(error):
            gql_utils.get_tx_status_once("http://n", "ab" * 32, None, 5)


def test_single_shot_queries_call_once_with_timeout():
    bodies = [
        {"data": {"nodeStatus": {"tip": {"index": 42}}}},
        {"data": {"transaction": {"nextTxNonce": 7}}},
        {"data": {"transaction": {"transactionResult": {"txStatus": "STAGING"}}}},
    ]
    with patch.object(
        gql_utils.requests, "post", side_effect=[fake_response(b) for b in bodies]
    ) as post:
        assert gql_utils.get_tip_index_once("http://n", None, 5) == 42
        assert gql_utils.get_next_tx_nonce_once("http://n", SIGNER, None, 5) == 7
        assert gql_utils.get_tx_status_once("http://n", "ab" * 32, None, 5) == "STAGING"
    assert post.call_count == 3
    assert all(c.kwargs["timeout"] == 5 for c in post.call_args_list)
    # JWT 비밀값이 없으면 헤더를 붙이지 않는다(검증자는 JWT 를 보지 않는다).
    assert all(c.kwargs["headers"] == {} for c in post.call_args_list)


# --- 시험 26·27: 서명 함수 분리 뒤 consumer 결과가 같다 ------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_action_id(monkeypatch):
    """GrantItems 의 action id 는 uuid1 이라 매번 다르다. 바이트 비교를 위해 고정한다."""
    monkeypatch.setattr(actions, "uuid1", lambda: SimpleNamespace(hex="ab" * 16))


def old_style_signed_tx(nonce, normal_levels=(1,)):
    """분리 전 consumer 가 하던 그대로(ca03895 claim_consumer.py:139-175)."""
    memo = build_claim_memo(list(normal_levels), [], "CouragePass")
    unsigned = create_grant_items_unsigned_tx(
        planet_id=ODIN,
        public_key=PUBKEY.hex(),
        address=SIGNER,
        nonce=nonce,
        avatar_addr=AVATAR,
        claim_data=REWARD,
        memo=memo,
        timestamp=FIXED,
    )
    return create_signed_tx(unsigned, SIGNATURE)


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED


def consume(nonce, chain_nonce=7):
    claim = SimpleNamespace(
        id=1,
        uuid="c1",
        planet_id=ODIN,
        nonce=nonce,
        tx=None,
        tx_id=None,
        tx_status=None,
        normal_levels=[1],
        premium_levels=[],
        season_pass=SimpleNamespace(pass_type=SimpleNamespace(value="CouragePass")),
        avatar_addr=AVATAR,
        reward_list=REWARD,
    )
    sess = MagicMock()
    sess.scalars.return_value = [claim]
    sess.scalar.return_value = None
    gql = MagicMock()
    gql.get_next_nonce.return_value = chain_nonce
    gql.stage.return_value = (True, "", None)
    account = SimpleNamespace(
        address=SIGNER, pubkey=PUBKEY, sign_tx=MagicMock(return_value=SIGNATURE)
    )
    with patch.object(
        claim_consumer, "scoped_session", return_value=sess
    ), patch.object(claim_consumer, "_get_account", return_value=account), patch.object(
        claim_consumer, "GQLClient", return_value=gql
    ), patch.object(
        claim_consumer, "history_table_ready", return_value=False, create=True
    ), patch.object(
        claim_consumer, "datetime", FixedDatetime
    ), patch.object(
        claim_consumer, "logger"
    ):
        claim_consumer.consume_claim_message(ClaimMessage(uuid="c1"))
    return claim, gql


def test_consumer_tx_is_byte_identical_after_split():
    claim, gql = consume(None)
    expected = old_style_signed_tx(7)
    assert claim.tx == expected.hex()
    assert claim.tx_id == hashlib.sha256(expected).hexdigest()
    assert claim.nonce == 7
    assert gql.stage.call_args.args[1] == expected


def test_build_signed_claim_tx_matches_old_composition():
    from app.utils import claim_tx

    fields = claim_tx.ClaimTxFields(
        planet_id=ODIN,
        avatar_addr=AVATAR,
        reward_list=REWARD,
        normal_levels=[1],
        premium_levels=[],
        pass_type="CouragePass",
    )
    account = SimpleNamespace(
        address=SIGNER, pubkey=PUBKEY, sign_tx=MagicMock(return_value=SIGNATURE)
    )
    assert claim_tx.build_signed_claim_tx(fields, 9, account, FIXED) == (
        old_style_signed_tx(9)
    )
    # 시각은 노드가 만료를 판단하는 t 필드 그대로다.
    assert claim_tx.tx_signed_at(old_style_signed_tx(9).hex()) == FIXED
    assert claim_tx.tx_signed_at("zz") is None
    assert claim_tx.tx_signed_at(None) is None


def test_consumer_reuses_nonce_zero_with_split_signer():
    claim, _ = consume(0)
    assert claim.nonce == 0
    assert claim.tx == old_style_signed_tx(0).hex()
