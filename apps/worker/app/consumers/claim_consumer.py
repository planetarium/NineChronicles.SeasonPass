# Receive message from SQS and send season pass reward
import hashlib
import json
from datetime import datetime, timezone

import structlog
from app.config import config
from app.utils.aws import Account
from shared.enums import TxStatus
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage
from shared.utils._graphql import GQLClient
from shared.utils.transaction import create_grant_items_unsigned_tx, create_signed_tx
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)
engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def consume_claim_message(message: ClaimMessage):
    """
    # SeasonPass claim handler

    Receive claim request messages from SQS and send rewards.
    The original claim data (in RDB) is already created and this function only reads data and create Tx. to the chain.
    In the case of re-treat(nonce has been assigned), handler will reuse assigned nonce.
    To create brand-new Tx, you should erase former nonce before send new message.
    """

    sess = scoped_session(sessionmaker(bind=engine))
    account = Account(config.kms_key_id, config.region_name)
    gql = GQLClient(
        config.converted_gql_url_map,
        config.headless_jwt_secret,
        timeout=config.gql_timeout,
    )

    try:
        claim_dict = {
            x.uuid: x
            for x in sess.scalars(select(Claim).where(Claim.uuid == message.uuid))
        }
        target_claim_list = []
        nonce_dict = {}

        use_nonce = False
        claim = claim_dict.get(message.uuid)
        if not claim:
            logger.error(f"Cannot find claim {message.uuid}")
            return
        if claim.planet_id not in nonce_dict:
            nonce = max(
                gql.get_next_nonce(claim.planet_id, account.address),
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
        else:
            nonce = nonce_dict[claim.planet_id]

        if claim.tx:
            target_claim_list.append(claim)
            return

        if not claim.nonce:
            claim.nonce = nonce
            use_nonce = True
        claim.tx_status = TxStatus.CREATED

        # GQL 의존성 제거: 로컬에서 unsigned transaction 생성
        memo = json.dumps(
            {
                "season_pass": {
                    "n": claim.normal_levels,
                    "p": claim.premium_levels,
                    "t": "claim",
                    "tp": claim.season_pass.pass_type.value,
                }
            }
        )

        unsigned_tx = create_grant_items_unsigned_tx(
            planet_id=claim.planet_id,
            public_key=account.pubkey.hex(),
            address=account.address,
            nonce=claim.nonce,
            avatar_addr=claim.avatar_addr,
            claim_data=claim.reward_list,
            memo=memo,
            # ⚠️ 클레임 생성 시각이 아니라 **지금**이어야 한다. 이 값은 tx 의 t 필드로
            #   들어가고 체인은 너무 과거인 tx 를 스테이징에서 거부한다. 클레임이 만들어진
            #   직후에 처리되면 둘이 같아서 티가 안 나지만, 재시도가 늦어지면 태어날 때부터
            #   만료된 tx 가 된다 — 그리고 그 tx 가 nonce 를 선점하므로 **뒤따르는 클레임이
            #   전부 막힌다**(구멍 뒤의 tx 는 스테이징에서 밀려나 차례로 INVALID 가 된다).
            #
            #   2026-09-29 Heimdall 지급 정지가 이것이었다. beat 데드락으로 2.7일 묶여 있던
            #   클레임이 풀려나면서 2.7일 전 타임스탬프를 단 tx 를 만들었고, 그게 거부되며
            #   nonce 1794589 에 구멍이 생겨 뒤의 클레임이 전부 섰다.
            #
            #   평소에 이게 안 터진 건 transaction.py 의 strftime 이 tzinfo 를 버리고 'Z' 를
            #   붙여서다 — created_at 이 KST(+09)로 와서 모든 tx 가 실제보다 9시간 미래로
            #   찍혔고, 그 우연한 여유분이 유일한 완충이었다. 즉 생성 후 9시간이 지나서
            #   처리되는 클레임은 원래부터 전부 만료된 tx 가 됐다.
            #
            #   now(tz=utc) 를 쓰면 strftime 결과도 진짜 UTC 가 되어 두 문제가 같이 없어진다.
            #   같은 저장소의 burn_asset_task 가 이미 이렇게 한다.
            timestamp=datetime.now(tz=timezone.utc),
        )

        # AWS KMS로 서명 생성
        signature = account.sign_tx(unsigned_tx)

        # GQL 의존성 제거: 로컬에서 signed transaction 생성
        signed_tx = create_signed_tx(unsigned_tx, signature)

        tx_id = hashlib.sha256(signed_tx).hexdigest()
        claim.tx = signed_tx.hex()
        claim.tx_id = tx_id
        sess.add(claim)
        target_claim_list.append(claim)
        if use_nonce:
            nonce_dict[claim.planet_id] += 1
        sess.commit()

        for claim in target_claim_list:
            success, msg, _ = gql.stage(claim.planet_id, bytes.fromhex(claim.tx))
            if not success:
                message = f"Failed to stage tx with nonce {claim.nonce}: {msg}"
                logger.error(message)
                raise Exception(message)
            claim.tx_status = TxStatus.STAGED
            sess.add(claim)
            sess.commit()
    finally:
        sess.close()
