# Receive message from SQS and send season pass reward
import hashlib
import threading
import time
from datetime import datetime, timezone

import structlog
from app.config import config
from app.utils.aws import Account
from app.utils.claim_tx import ClaimTxFields, build_unsigned_claim_tx
from app.utils.nonce_lock import lock_planet_nonce, set_lock_timeout
from shared.enums import TxStatus
from shared.models.user import Claim, ClaimTxHistory
from shared.schemas.message import ClaimMessage
from shared.utils._graphql import GQLClient
from shared.utils.claim_tx_history import history_table_ready
from shared.utils.transaction import create_signed_tx
from sqlalchemy import create_engine, desc, exists, select
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)

_account = None
_account_lock = threading.Lock()


def _get_account() -> Account:
    """KMS Account 를 프로세스당 한 번만 만든다(공개키 조회에 메시지당 0.6초가 들었다).
    공개키·주소는 키가 같으면 변하지 않고, 서명은 매번 KMS 가 한다. 생성 실패는 캐시하지 않는다."""
    global _account
    if _account is None:
        with _account_lock:
            if _account is None:
                _account = Account(config.kms_key_id, config.region_name)
    return _account


def _has_history(sess, claim_id: int) -> bool:
    """재서명 기록(released 제외)이 있는지. SAVEPOINT 안에서 묻는다 — 실패해도(잠금 시간 초과 등)
    claim 행을 FOR UPDATE 로 쥔 바깥 트랜잭션은 살아 있고 지급을 이어 간다.

    실패를 "막지 않음"으로 보는 이유: 권한은 history_table_ready 가 미리 확인하므로 남는 실패는
    일시적인 것뿐이고, 실패를 "막음"으로 보면 잠금 문제 하나로 모든 플래닛의 새 지급이 멈춘다."""
    try:
        with sess.begin_nested():
            return bool(
                sess.scalar(
                    select(
                        exists().where(
                            ClaimTxHistory.claim_id == claim_id,
                            ClaimTxHistory.reason.in_(("expired", "lost")),
                        )
                    )
                )
            )
    except Exception as e:
        logger.error(
            "claim_tx_history check failed; continuing", claim_id=claim_id, exc_info=e
        )
        return False


def _nonce_reset_blocked(sess, claim) -> bool:
    """nonce 가 빈 claim 에 새 nonce 를 주면 안 되는지.

    - 상태 기반: consumer 는 tx·nonce·CREATED 를 한 commit 에 적으므로, 상태가 남아 있는데 nonce 가
      비었으면 tx 가 나간 뒤 사람이 nonce 를 비운 행이다. 기본값 off 에서 동작이 바뀌지 않게
      on 에서만 막고 dry_run 은 경고만 한다.
    - 기록 기반: 재서명 기록(released 제외)이 있으면 모드와 상관없이 막는다. 기록 행은 재서명이
      켜졌을 때만 생기므로 지금 데이터에는 영향이 없다.
    대응 절차: 9c-ops season-pass.md 의 nonce_reset_blocked."""
    if claim.tx_status is not None:
        if config.resign_mode == "on":
            logger.error(
                "Refusing to assign a new nonce to a claim whose tx was sent",
                planet_id=claim.planet_id,
                claim_id=claim.id,
                basis="status",
            )
            return True
        if config.resign_mode == "dry_run":
            logger.warning(
                "Would refuse nonce reset",
                planet_id=claim.planet_id,
                claim_id=claim.id,
                basis="status",
            )
    if history_table_ready(engine) and _has_history(sess, claim.id):
        logger.error(
            "Refusing to assign a new nonce to a claim whose tx was sent",
            planet_id=claim.planet_id,
            claim_id=claim.id,
            basis="history",
        )
        return True
    return False


def consume_claim_message(message: ClaimMessage):
    """
    # SeasonPass claim handler

    Receive claim request messages from SQS and send rewards.
    The original claim data (in RDB) is already created and this function only reads data and create Tx. to the chain.
    In the case of re-treat(nonce has been assigned), handler will reuse assigned nonce.
    To create brand-new Tx, you should erase former nonce before send new message.
    """

    # 병목 측정용: 구간별 소요 시간(초)을 끝에 한 줄로 남긴다. 동작은 바꾸지 않는다.
    timings = {}
    t_mark = time.perf_counter()

    def lap(name: str):
        nonlocal t_mark
        now = time.perf_counter()
        timings[name] = round(now - t_mark, 3)
        t_mark = now

    sess = scoped_session(sessionmaker(bind=engine))
    outcome = "error"
    planet_id = None

    try:
        # FOR UPDATE: API 가 보낸 메시지와 beat 재처리(retry_claim)가 같은 claim 을 동시에 잡으면
        # 둘 다 tx 가 없다고 보고 각자 서명해 같은 nonce 로 tx 두 개를 stage 했다. 행 잠금으로
        # 뒤에 온 쪽은 앞의 commit 을 기다렸다가 tx 가 채워진 행을 다시 읽고 아래에서 끝난다.
        # 잠금은 tx 를 적는 commit(또는 실패 시 close 의 rollback)에서 풀린다.
        # 기다림은 LOCK_TIMEOUT 까지다(죽은 파드 세션이 쥔 잠금에 무한히 묶이지 않게, nonce_lock.py).
        set_lock_timeout(sess)
        claim_dict = {
            x.uuid: x
            for x in sess.scalars(
                select(Claim).where(Claim.uuid == message.uuid).with_for_update()
            )
        }
        target_claim_list = []
        nonce_dict = {}

        use_nonce = False
        claim = claim_dict.get(message.uuid)
        if not claim:
            outcome = "no_claim"
            logger.error(f"Cannot find claim {message.uuid}")
            return
        planet_id = claim.planet_id
        lap("db_read_claim")

        # 이미 tx 가 만들어진 claim(beat 재처리가 먼저 처리한 큐 메시지 등)은 할 일이 없다.
        # KMS·GQL 준비와 nonce 조회 전에 바로 끝낸다.
        if claim.tx:
            outcome = "skip_tx_exists"
            return

        # tx 가 한 번이라도 나간 claim 에 새 nonce 를 주지 않는다(재서명 B2 의 nonce 불변).
        # 사람이 nonce 를 비우면(예전 복구 습관) 옛 tx 와 새 nonce 의 tx 가 둘 다 블록에 들어가
        # 같은 보상이 두 번 지급될 수 있다. KMS·GQL 준비 전에 판단한다.
        if claim.nonce is None and _nonce_reset_blocked(sess, claim):
            outcome = "nonce_reset_blocked"
            return

        account = _get_account()
        lap("kms_init")
        gql = GQLClient(
            config.converted_gql_url_map,
            config.headless_jwt_secret,
            timeout=config.gql_timeout,
        )
        lap("gql_init")

        # 서로 다른 claim 이 같은 플래닛 nonce 를 동시에 계산하지 않게, nonce 계산부터 tx 를 적는
        # commit 까지 플래닛 잠금을 쥔다(DB 최대 nonce 는 commit 뒤에야 다음 계산에 보인다).
        # 예전엔 (planet_id, nonce) 유일 제약이 서명까지 마친 뒤 commit 에서야 충돌을 잡았다.
        # 잠금을 쥔 채 nonce GQL·KMS 서명(수 초)을 하지만 워커 동시 처리가 1이라 감수한다.
        lock_planet_nonce(sess, claim.planet_id)
        lap("nonce_lock")

        if claim.planet_id not in nonce_dict:
            chain_nonce = gql.get_next_nonce(claim.planet_id, account.address)
            lap("nonce_gql")
            nonce = max(
                chain_nonce,
                (
                    sess.scalar(
                        select(Claim.nonce)
                        .where(
                            Claim.nonce.is_not(None),
                            Claim.planet_id == claim.planet_id,
                        )
                        .order_by(desc(Claim.nonce))
                        .limit(1)
                    )
                    or -1
                )
                + 1,
            )
            nonce_dict[claim.planet_id] = nonce
            lap("nonce_db")
        else:
            nonce = nonce_dict[claim.planet_id]

        # nonce 0 도 배정된 값이다(`not claim.nonce` 는 0 을 비었다고 보고 새 nonce 를 줬다).
        if claim.nonce is None:
            claim.nonce = nonce
            use_nonce = True
        claim.tx_status = TxStatus.CREATED

        # 메모 → unsigned tx → KMS 서명 → signed tx. 재서명(stage_task)의 build_signed_claim_tx 와
        # 같은 단계를 claim_timing 구간별로 나눠 부른다. 시각은 언제나 지금이어야 한다(이유는
        # build_unsigned_claim_tx 주석).
        unsigned_tx = build_unsigned_claim_tx(
            ClaimTxFields.from_claim(claim),
            claim.nonce,
            account.pubkey,
            account.address,
            timestamp=datetime.now(tz=timezone.utc),
        )
        lap("build_unsigned")

        # AWS KMS로 서명 생성
        signature = account.sign_tx(unsigned_tx)
        lap("kms_sign")

        signed_tx = create_signed_tx(unsigned_tx, signature)

        tx_id = hashlib.sha256(signed_tx).hexdigest()
        claim.tx = signed_tx.hex()
        claim.tx_id = tx_id
        sess.add(claim)
        target_claim_list.append(claim)
        if use_nonce:
            nonce_dict[claim.planet_id] += 1
        sess.commit()
        lap("commit_tx")

        for claim in target_claim_list:
            success, msg, _ = gql.stage(claim.planet_id, bytes.fromhex(claim.tx))
            lap("stage_gql")
            if not success:
                # message(ClaimMessage)를 덮어쓰지 않는다 — finally 의 claim_timing 이 uuid 를 쓴다.
                error_message = f"Failed to stage tx with nonce {claim.nonce}: {msg}"
                logger.error(
                    error_message, planet_id=claim.planet_id, nonce=claim.nonce
                )
                raise Exception(error_message)
            claim.tx_status = TxStatus.STAGED
            sess.add(claim)
            sess.commit()
            lap("commit_staged")
        outcome = "staged"
    except Exception as exc:
        # process_claim 의 실패 로그("Error processing claim message")가 어느 플래닛인지 남길 수
        # 있게 예외에 실어 보낸다. claim 을 읽기 전에 실패하면 None 이다.
        exc.planet_id = planet_id
        raise
    finally:
        # 성공·조기 종료·실패 모두 남긴다(시간 숫자·결과 이름·플래닛·claim uuid 만, 아바타 주소 같은
        # 사용자 식별 정보 없음). 플래닛은 KMS·nonce 조회 실패처럼 여기서 따로 로그를 남기지 않는
        # 실패가 어느 플래닛인지 보려고, uuid 는 process_claim 실패 로그와 짝을 지으려고 붙인다.
        logger.info(
            "claim_timing",
            outcome=outcome,
            uuid=message.uuid,
            planet_id=planet_id,
            **timings,
            total=round(sum(timings.values()), 3),
        )
        sess.close()
