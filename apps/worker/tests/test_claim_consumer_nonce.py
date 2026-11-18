"""consume_claim_message — 이미 배정된 nonce 를 그대로 다시 쓴다(0 포함)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.consumers import claim_consumer
from shared.enums import PlanetID
from shared.schemas.message import ClaimMessage


def run_consume(claim, chain_nonce=7):
    sess = MagicMock()
    sess.scalars.return_value = [claim]
    sess.scalar.return_value = None
    gql = MagicMock()
    gql.get_next_nonce.return_value = chain_nonce
    gql.stage.return_value = (True, "", None)
    account = SimpleNamespace(
        address="0x" + "12" * 20,
        pubkey=bytes.fromhex("02" + "ab" * 32),
        sign_tx=MagicMock(return_value=b"\x30" * 70),
    )
    with patch.object(
        claim_consumer, "scoped_session", return_value=sess
    ), patch.object(claim_consumer, "_get_account", return_value=account), patch.object(
        claim_consumer, "GQLClient", return_value=gql
    ), patch.object(
        claim_consumer, "history_table_ready", return_value=False
    ), patch.object(
        claim_consumer, "build_unsigned_claim_tx", return_value=b"unsigned"
    ) as build, patch.object(
        claim_consumer, "create_signed_tx", return_value=b"signed"
    ), patch.object(
        claim_consumer, "logger"
    ):
        claim_consumer.consume_claim_message(ClaimMessage(uuid=claim.uuid))
    return build.call_args.args[1], sess


def make_claim(nonce):
    return SimpleNamespace(
        uuid="c1",
        planet_id=PlanetID.ODIN.value,
        nonce=nonce,
        tx=None,
        tx_id=None,
        tx_status=None,
        normal_levels=[1],
        premium_levels=[],
        season_pass=SimpleNamespace(pass_type=SimpleNamespace(value="CouragePass")),
        avatar_addr="0x" + "34" * 20,
        reward_list=[],
    )


def test_reuses_assigned_nonce_zero():
    # 플래닛의 첫 tx 는 nonce 0 이다. 비었다고 보고 새 nonce(7)를 주면 0 이 구멍으로 남는다.
    claim = make_claim(0)
    nonce, _ = run_consume(claim)
    assert nonce == 0 and claim.nonce == 0


def test_assigns_new_nonce_when_none():
    claim = make_claim(None)
    nonce, _ = run_consume(claim)
    assert nonce == 7 and claim.nonce == 7


def test_takes_planet_lock_and_row_lock():
    _, sess = run_consume(make_claim(None))
    stmt = sess.scalars.call_args.args[0]
    assert stmt._for_update_arg is not None  # SELECT … FOR UPDATE
    sql = [str(c.args[0]) for c in sess.execute.call_args_list]
    assert any("pg_advisory_xact_lock" in s for s in sql)
