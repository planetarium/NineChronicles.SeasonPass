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
import requests
from app.config import config
from app.tasks import burn_asset_task
from app.tasks.burn_asset_task import get_decimal_places_for_ticker, process_burn_asset
from celery.exceptions import Retry

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
    ) as session_cls, patch.object(
        burn_asset_task.requests, "post"
    ) as post, patch.object(
        config, "burn_alert_webhook_url", None
    ):
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

        # Only a failure before stage may retry (the signed tx never left the process); a
        # retry after stage could burn twice. retry returns a Retry, as Celery's does.
        with patch.object(
            process_burn_asset,
            "retry",
            side_effect=lambda exc, countdown: Retry(exc=exc),
        ) as retry:
            yield SimpleNamespace(
                logger=logger,
                account_cls=account_cls,
                account=account,
                gql=gql,
                sess=sess,
                retry=retry,
                post=post,
            )


def last_attempt(message):
    """Run as the attempt after the last automatic retry (request.retries == max)."""
    process_burn_asset.push_request(retries=burn_asset_task.PRE_STAGE_RETRIES)
    try:
        return process_burn_asset.run(message)
    finally:
        process_burn_asset.pop_request()


def assert_retried_before_stage(env):
    env.retry.assert_called_once()
    assert (
        env.retry.call_args.kwargs["countdown"]
        == burn_asset_task.PRE_STAGE_RETRY_SECONDS
    )
    env.gql.stage.assert_not_called()
    env.logger.error.assert_not_called()
    call = env.logger.warning.call_args
    assert "not burned, retrying" in call.args[0]
    assert call.kwargs["stage_attempted"] is False


def error_call(logger):
    """The single failure log line (event, kwargs)."""
    assert logger.error.call_count == 1, logger.error.call_args_list
    call = logger.error.call_args
    return call.args[0], call.kwargs


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


@pytest.mark.parametrize(
    "message",
    [
        {"amount": "10.5"},
        {"ticker": "", "amount": "10.5"},
        {"ticker": "NCG", "amount": "invalid_amount"},
        {"ticker": "NCG", "amount": None},
        {"ticker": "NCG", "amount": "0"},
        {"ticker": "NCG", "amount": "-1"},
        {"ticker": "NCG", "amount": "NaN"},
        {"ticker": "NCG", "amount": "1", "planet_id": "0x999999999999"},
    ],
    ids=[
        "no-ticker",
        "empty-ticker",
        "amount-text",
        "amount-none",
        "amount-zero",
        "amount-negative",
        "amount-nan",
        "unknown-planet",
    ],
)
def test_invalid_request_fails_at_once_without_retry(env, message):
    # Retrying a wrong request cannot help: fail on the first attempt, before any work.
    with pytest.raises(burn_asset_task.BurnRequestError):
        process_burn_asset(message)

    env.retry.assert_not_called()
    env.account_cls.assert_not_called()
    env.gql.get_next_nonce.assert_not_called()
    env.gql.stage.assert_not_called()
    event, kw = error_call(env.logger)
    assert "request is invalid; not burned" in event
    assert kw["stage_attempted"] is False and kw["retries"] == 0


def test_account_error_raises(env):
    env.account_cls.side_effect = Exception("Account creation failed")
    with pytest.raises(Exception, match="Account creation failed"):
        last_attempt({"ticker": "NCG", "amount": "10.5"})
    env.gql.stage.assert_not_called()


def test_task_options():
    # acks_late=False: the worker acks on receipt, so a worker killed after stage does not
    # get the message redelivered (task_reject_on_worker_lost=True globally) and burn again.
    assert process_burn_asset.acks_late is False
    assert process_burn_asset.max_retries == burn_asset_task.PRE_STAGE_RETRIES == 3


def test_success_logs_planet(env):
    process_burn_asset({"ticker": "NCG", "amount": "1", "planet_id": "0x000000000001"})
    events = {c.args[0]: c.kwargs for c in env.logger.info.call_args_list}
    created = events["Burn asset transaction created successfully"]
    staged = events["Burn asset transaction staged successfully"]
    assert created["planet_id"] == staged["planet_id"] == "0x000000000001"
    assert created["nonce"] == 123 and created["tx_id"] == staged["tx_id"]


def test_stage_rejected_is_not_retried_and_says_may_have_burned(env):
    env.gql.stage.return_value = (False, "node said no", None)

    with pytest.raises(Exception, match="Failed to stage burn asset tx"):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    env.retry.assert_not_called()
    env.gql.stage.assert_called_once()
    event, kw = error_call(env.logger)
    assert "MAY HAVE BEEN BURNED" in event
    assert kw["stage_attempted"] is True
    assert kw["planet_id"] == "0x000000000000"
    assert kw["nonce"] == 123 and kw["tx_id"]
    assert kw["msg"] == "node said no"


def test_stage_timeout_is_not_retried(env):
    env.gql.stage.side_effect = requests.exceptions.ReadTimeout("read timed out")

    with pytest.raises(requests.exceptions.ReadTimeout):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    env.retry.assert_not_called()
    env.gql.stage.assert_called_once()
    event, kw = error_call(env.logger)
    assert "MAY HAVE BEEN BURNED" in event
    assert kw["stage_attempted"] is True and kw["tx_id"]


def test_node_nonce_unavailable_stops_before_signing(env):
    # get_next_nonce returns -1 on a GQL error. max(-1, DB+1) used to sign with the DB nonce,
    # which can collide with a claim still in the node's queue.
    env.gql.get_next_nonce.return_value = -1

    with pytest.raises(Retry):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    env.account.sign_tx.assert_not_called()
    assert_retried_before_stage(env)
    assert env.logger.warning.call_args.kwargs["planet_id"] == "0x000000000000"


def test_kms_error_retries_before_stage(env):
    env.account.sign_tx.side_effect = RuntimeError("KMS throttled")

    with pytest.raises(Retry):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    assert_retried_before_stage(env)
    assert isinstance(env.retry.call_args.kwargs["exc"], RuntimeError)


def test_pre_stage_failure_after_last_retry_logs_error(env):
    env.gql.get_next_nonce.return_value = -1

    with pytest.raises(RuntimeError, match="nextTxNonce unavailable"):
        last_attempt({"ticker": "NCG", "amount": "1"})

    env.retry.assert_not_called()
    env.account.sign_tx.assert_not_called()
    event, kw = error_call(env.logger)
    assert "not burned, safe to request again" in event
    assert kw["stage_attempted"] is False
    assert kw["retries"] == burn_asset_task.PRE_STAGE_RETRIES
    assert kw["planet_id"] == "0x000000000000"


def test_stage_failure_on_a_retried_attempt_is_not_retried(env):
    # A pre-stage retry that gets as far as stage is under the same rule: no retry after stage.
    env.gql.stage.side_effect = requests.exceptions.ReadTimeout("read timed out")
    process_burn_asset.push_request(retries=1)
    try:
        with pytest.raises(requests.exceptions.ReadTimeout):
            process_burn_asset.run({"ticker": "NCG", "amount": "1"})
    finally:
        process_burn_asset.pop_request()

    env.retry.assert_not_called()
    event, kw = error_call(env.logger)
    assert "MAY HAVE BEEN BURNED" in event
    assert kw["stage_attempted"] is True and kw["retries"] == 1


WEBHOOK = "https://hooks.example/services/T/B/x"


def posted_text(env):
    env.post.assert_called_once()
    (url,), kw = env.post.call_args
    assert url == WEBHOOK
    assert kw["timeout"] == burn_asset_task.ALERT_TIMEOUT_SECONDS
    return kw["json"]["text"]


def test_failure_after_stage_alerts(env, monkeypatch):
    # The requester got "success" when the message was queued; the alert is how a person learns.
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)
    env.gql.stage.side_effect = requests.exceptions.ReadTimeout("read timed out")

    with pytest.raises(requests.exceptions.ReadTimeout):
        process_burn_asset(
            {"ticker": "NCG", "amount": "1", "planet_id": "0x000000000001"}
        )

    text = posted_text(env)
    event, kw = error_call(env.logger)
    assert event in text and "MAY HAVE BEEN BURNED" in text
    assert f"tx_id={kw['tx_id']}" in text and "nonce=123" in text
    assert "planet_id=0x000000000001" in text and "stage_attempted=True" in text
    assert "error=ReadTimeout" in text
    assert kw["alerted"] is True


def test_failure_before_stage_alerts_after_last_retry(env, monkeypatch):
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)
    env.gql.get_next_nonce.return_value = -1

    with pytest.raises(RuntimeError):
        last_attempt({"ticker": "NCG", "amount": "1"})

    text = posted_text(env)
    assert "not burned, safe to request again" in text
    assert "stage_attempted=False" in text and "retries=3" in text


def test_invalid_request_alerts(env, monkeypatch):
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)

    with pytest.raises(burn_asset_task.BurnRequestError):
        process_burn_asset({"ticker": "NCG", "amount": "0"})

    assert "request is invalid" in posted_text(env)


def test_retry_does_not_alert(env, monkeypatch):
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)
    env.gql.get_next_nonce.return_value = -1

    with pytest.raises(Retry):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    env.post.assert_not_called()


def test_no_webhook_logs_only(env):
    env.gql.stage.return_value = (False, "node said no", None)

    with pytest.raises(Exception, match="Failed to stage"):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    env.post.assert_not_called()
    _, kw = error_call(env.logger)
    assert kw["alerted"] is False


def test_alert_failure_keeps_the_task_failure(env, monkeypatch):
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)
    env.post.side_effect = requests.exceptions.ConnectionError(f"{WEBHOOK} refused")
    env.gql.stage.return_value = (False, "node said no", None)

    with pytest.raises(Exception, match="Failed to stage"):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    events = [c.args[0] for c in env.logger.error.call_args_list]
    assert events[0] == "Burn asset failure alert not sent"
    assert "MAY HAVE BEEN BURNED" in events[1]
    assert env.logger.error.call_args.kwargs["alerted"] is False
    assert all(WEBHOOK not in str(c) for c in env.logger.error.call_args_list)


def test_webhook_error_status_is_not_alerted(env, monkeypatch):
    monkeypatch.setattr(config, "burn_alert_webhook_url", WEBHOOK)
    env.post.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "404"
    )
    env.gql.stage.return_value = (False, "node said no", None)

    with pytest.raises(Exception, match="Failed to stage"):
        process_burn_asset({"ticker": "NCG", "amount": "1"})

    assert env.logger.error.call_args.kwargs["alerted"] is False
