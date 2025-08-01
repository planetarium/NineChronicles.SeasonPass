# Receive message from SQS and send season pass reward
import hashlib
import json

import structlog
from app.config import config
from app.utils.aws import Account
from shared.enums import TxStatus
from shared.models.user import Claim
from shared.schemas.message import ClaimMessage
from shared.utils._graphql import GQLClient
from shared.utils.transaction import create_claim_items_unsigned_tx, create_signed_tx
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
    gql = GQLClient(config.converted_gql_url_map, config.headless_jwt_secret)

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

        unsigned_tx = create_claim_items_unsigned_tx(
            planet_id=claim.planet_id,
            public_key=account.pubkey.hex(),
            address=account.address,
            nonce=claim.nonce,
            avatar_addr=claim.avatar_addr,
            claim_data=claim.reward_list,
            memo=memo,
            timestamp=claim.created_at,
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
