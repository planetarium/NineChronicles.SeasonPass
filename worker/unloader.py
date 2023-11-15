# Receive message from SQS and send season pass reward
import json
import os
from datetime import timezone, datetime, timedelta

from sqlalchemy import create_engine, select, desc
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import TxStatus
from common.models.user import Claim
from common.utils.aws import fetch_secrets, fetch_kms_key_id
from consts import ITEM_FUNGIBLE_ID_DICT
from schemas.sqs import SQSMessage
from common.utils._crypto import Account
from common.utils._graphql import GQL

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

stage = os.environ.get("STAGE", "development")
region_name = os.environ.get("REGION_NAME", "us-east-2")
engine = create_engine(DB_URI, pool_size=5, max_overflow=5)


def handle(event, context):
    message = SQSMessage(Records=event.get("Records", []))
    sess = None
    account = Account(fetch_kms_key_id(stage, region_name))
    gql = GQL()

    try:
        sess = scoped_session(sessionmaker(bind=engine))

        uuid_list = [x.body.get("uuid") for x in message.Records if x.body.get("uuid") is not None]
        claim_dict = {x.uuid: x for x in sess.scalars(select(Claim).where(Claim.uuid.in_(uuid_list)))}
        target_claim_list = []
        nonce_dict = {}

        for i, record in enumerate(message.Records):
            claim = claim_dict.get(record.body.get("uuid"))
            if not claim:
                logger.error(f"Cannot find claim {record.body.get('uuid')}")
                continue
            if claim.planet_id not in nonce_dict:
                nonce = max(
                    gql.get_next_nonce(claim.planet_id, account.address),
                    (sess.scalar(select(Claim.nonce).where(
                        Claim.nonce.is_not(None),
                        Claim.planet_id == claim.planet_id,
                    ).order_by(desc(Claim.nonce)).limit(1)) or -1) + 1
                )
                nonce_dict[claim.planet_id] = nonce
            else:
                nonce = nonce_dict[claim.planet_id]

            claim.nonce = nonce
            claim.tx_status = TxStatus.CREATED
            if "claims" in claim.reward_list:
                unsigned_tx = gql.create_action(
                    claim.planet_id,
                    "claim_items", pubkey=account.pubkey, nonce=nonce,
                    avatar_addr=claim.avatar_addr,
                    claim_items=claim.reward_list["claims"],
                )
            else:
                unsigned_tx = gql.create_action(
                    claim.planet_id,
                    "unload_from_garage", pubkey=account.pubkey, nonce=nonce,
                    avatar_addr=claim.avatar_addr,
                    fav_data=[{
                        "balanceAddr": claim.agent_addr,
                        "value": {"currencyTicker": ticker, "value": f"{amount}"}
                    } for ticker, amount in claim.reward_list["currency"].items()],
                    item_data=[{
                        "fungibleId": ITEM_FUNGIBLE_ID_DICT[item_id],
                        "count": f"{amount}"
                    } for item_id, amount in claim.reward_list["item"].items()],
                    timestamp=(datetime.now(tz=timezone.utc) + timedelta(days=1)).isoformat(),
                    memo=json.dumps({"season_pass": {"n": claim.normal_levels, "p": claim.premium_levels,
                                                     "t": "claim"}}),
                )
            signature = account.sign_tx(unsigned_tx)
            signed_tx = gql.sign(claim.planet_id, unsigned_tx, signature)
            claim.tx = signed_tx.hex()
            sess.add(claim)
            target_claim_list.append(claim)

            nonce_dict[claim.planet_id] += 1
        sess.commit()

        for claim in target_claim_list:
            success, msg, tx_id = gql.stage(claim.planet_id, bytes.fromhex(claim.tx))
            if not success:
                message = f"Failed to stage tx with nonce {claim.nonce}: {msg}"
                logger.error(message)
                raise Exception(message)
            claim.tx_status = TxStatus.STAGED
            claim.tx_id = tx_id
            sess.add(claim)
            sess.commit()
    finally:
        if sess is not None:
            sess.close()
