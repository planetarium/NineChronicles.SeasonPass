"""process_burn_asset — builds, signs (KMS) and stages a burn_asset tx.

Every external dependency is mocked by the `env` fixture: KMS (Account), the
headless GraphQL client, the DB session, and the module-level structlog logger.
The task creates `logger` at import time, so it is patched directly — patching
structlog.get_logger afterwards would not reach it. Tests must never construct a
real Account: that calls AWS KMS with whatever credentials the runner has.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.tasks import burn_asset_task
from app.tasks.burn_asset_task import get_decimal_places_for_ticker, process_burn_asset

ADDRESS = "0x" + "12" * 20
PUBKEY = bytes.fromhex(
    "02" + "ab" * 32
)  # compressed secp256k1 public key, as Account.pubkey returns
SUCCESS = "Burn asset transaction processed and staged successfully"


@pytest.fixture
def env():
    with patch.object(burn_asset_task, "logger") as logger, patch.object(
        burn_asset_task, "Account"
    ) as account_cls, patch.object(
        burn_asset_task, "GQLClient"
    ) as gql_cls, patch.object(
        burn_asset_task, "scoped_session"
    ) as session_cls:
        account = MagicMock()
        account.address = ADDRESS
        account.pubkey = PUBKEY
        account.sign_tx.return_value = b"\x30" * 70
        account_cls.return_value = account

        gql = MagicMock()
        gql.get_next_nonce.return_value = 123
        gql.stage.return_value = (True, "Success", None)
        gql_cls.return_value = gql

        sess = MagicMock()
        sess.scalar.return_value = 100
        session_cls.return_value = sess

        yield SimpleNamespace(
            logger=logger, account_cls=account_cls, account=account, gql=gql, sess=sess
        )


def test_get_decimal_places_for_ticker():
    assert get_decimal_places_for_ticker("NCG") == 2
    assert get_decimal_places_for_ticker("CRYSTAL") == 18
    assert get_decimal_places_for_ticker("MEAD") == 18
    assert get_decimal_places_for_ticker("GOLD") == 0  # default
    assert get_decimal_places_for_ticker("UNKNOWN") == 0
    assert get_decimal_places_for_ticker("ncg") == 2  # case-insensitive


def test_success(env):
    message = {
        "ticker": "NCG",
        "amount": "10.5",
        "memo": "Test burn asset",
        "planet_id": "0x000000000000",
    }

    assert process_burn_asset(message) == SUCCESS

    env.account.sign_tx.assert_called_once()
    env.gql.stage.assert_called_once()
    planet, signed_tx = env.gql.stage.call_args.args
    assert planet.value == b"0x000000000000"
    assert isinstance(signed_tx, bytes) and signed_tx
    assert env.logger.info.call_count >= 2
    env.logger.error.assert_not_called()


def test_default_memo_and_planet(env):
    assert process_burn_asset({"ticker": "CRYSTAL", "amount": "5.0"}) == SUCCESS
    planet, _ = env.gql.stage.call_args.args
    assert planet.value == b"0x000000000000"  # ODIN by default


@pytest.mark.parametrize(
    "ticker,amount",
    [
        ("NCG", "100.25"),
        ("CRYSTAL", "1000.123456789012345678"),
        ("MEAD", "50.5"),
        ("GOLD", "7"),
    ],
)
def test_different_currencies(env, ticker, amount):
    with patch.object(
        burn_asset_task,
        "create_burn_asset_unsigned_tx",
        wraps=burn_asset_task.create_burn_asset_unsigned_tx,
    ) as build:
        assert process_burn_asset({"ticker": ticker, "amount": amount}) == SUCCESS
    kw = build.call_args.kwargs
    assert (kw["ticker"], kw["amount"], kw["decimal_places"]) == (
        ticker,
        amount,
        get_decimal_places_for_ticker(ticker),
    )
    assert kw["owner"] == ADDRESS and kw["public_key"] == PUBKEY.hex()


@pytest.mark.parametrize(
    "gql_nonce,db_nonce,expected", [(200, 150, 200), (100, 200, 201), (5, None, 5)]
)
def test_nonce_is_max_of_node_and_db(env, gql_nonce, db_nonce, expected):
    env.gql.get_next_nonce.return_value = gql_nonce
    env.sess.scalar.return_value = db_nonce
    with patch.object(
        burn_asset_task,
        "create_burn_asset_unsigned_tx",
        wraps=burn_asset_task.create_burn_asset_unsigned_tx,
    ) as build:
        process_burn_asset({"ticker": "NCG", "amount": "1"})
    assert build.call_args.kwargs["nonce"] == expected


def test_staging_failure_raises_and_logs(env):
    env.gql.stage.return_value = (False, "Staging failed", None)
    with pytest.raises(Exception) as exc_info:
        process_burn_asset({"ticker": "NCG", "amount": "10.5"})
    assert "Failed to stage burn asset tx" in str(exc_info.value)
    assert env.logger.error.call_count >= 1


def test_missing_ticker_raises(env):
    with pytest.raises(Exception):
        process_burn_asset({"amount": "10.5"})
    assert env.logger.error.call_count >= 1
    env.gql.stage.assert_not_called()


def test_invalid_amount_raises(env):
    with pytest.raises(Exception):
        process_burn_asset({"ticker": "NCG", "amount": "invalid_amount"})
    assert env.logger.error.call_count >= 1
    env.account.sign_tx.assert_not_called()
    env.gql.stage.assert_not_called()


def test_account_error_raises(env):
    env.account_cls.side_effect = Exception("Account creation failed")
    with pytest.raises(Exception, match="Account creation failed"):
        process_burn_asset({"ticker": "NCG", "amount": "10.5"})
    env.gql.stage.assert_not_called()
