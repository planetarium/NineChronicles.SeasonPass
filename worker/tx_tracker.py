import concurrent.futures
import json
import os
import boto3
from collections import defaultdict
from typing import Optional, Tuple

from datetime import datetime, timezone, timedelta
from gql.dsl import dsl_gql, DSLQuery
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, scoped_session

from common import logger
from common.enums import PlanetID, TxStatus
from common.models.user import Claim
from common.utils._graphql import GQL
from common.utils.aws import fetch_secrets

DB_URI = os.environ.get("DB_URI")
db_password = fetch_secrets(os.environ.get("REGION_NAME"), os.environ.get("SECRET_ARN"))["password"]
DB_URI = DB_URI.replace("[DB_PASSWORD]", db_password)

BLOCK_LIMIT = 200

engine = create_engine(DB_URI, pool_size=5, max_overflow=5)

SQS_URL = os.environ.get("SQS_URL")
sqs = boto3.client("sqs", region_name=os.environ.get("REGION_NAME"))


def process(planet_id: PlanetID, tx_id: str) -> Tuple[str, Optional[TxStatus], Optional[str]]:
    client = GQL(os.environ.get("HEADLESS_GQL_JWT_SECRET"))
    client.reset(planet_id)
    query = dsl_gql(
        DSLQuery(
            client.ds.StandaloneQuery.transaction.select(
                client.ds.TransactionHeadlessQuery.transactionResult.args(
                    txId=tx_id
                ).select(
                    client.ds.TxResultType.txStatus,
                    client.ds.TxResultType.blockIndex,
                    client.ds.TxResultType.blockHash,
                    client.ds.TxResultType.exceptionNames,
                )
            )
        )
    )
    resp = client.execute(query)
    logger.debug(resp)

    if "errors" in resp:
        logger.error(f"GQL failed to get transaction status: {resp['errors']}")
        return tx_id, TxStatus.INVALID, json.dumps(resp["errors"])

    try:
        return tx_id, TxStatus[resp["transaction"]["transactionResult"]["txStatus"]], json.dumps(
            resp["transaction"]["transactionResult"]["exceptionNames"])
    except:
        return tx_id, TxStatus.INVALID, json.dumps(resp["transaction"]["transactionResult"]["exceptionNames"])


def track_tx(event, context):
    logger.info("Tracking unfinished transactions")
    sess = scoped_session(sessionmaker(bind=engine))
    claim_list = sess.scalars(
        select(Claim).where(Claim.tx_status.in_((TxStatus.STAGED, TxStatus.INVALID,)))
        .order_by(Claim.id).limit(BLOCK_LIMIT)
    ).fetchall()
    if len(claim_list) == 0:
        logger.info("No claims to track status.")
        return

    result = defaultdict(list)

    now = datetime.now(tz=timezone.utc)
    created_claim_list = sess.scalars(
        select(Claim).where(
            Claim.created_at <= now - timedelta(minutes=5),
            Claim.tx_status == TxStatus.CREATED,
            Claim.reward_list != []
        ).order_by(Claim.id)
    ).fetchall()

    if created_claim_list:
        for created_claim in created_claim_list:
            msg = {
                "uuid": str(created_claim.uuid),
            }

            sqs.send_message(QueueUrl=SQS_URL, MessageBody=json.dumps(msg))
        logger.info(f"Found created claim [{len(created_claim_list)}], re queuing.")

    futures = {}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        for claim in claim_list:
            futures[executor.submit(process, PlanetID(claim.planet_id), claim.tx_id)] = claim

        for future in concurrent.futures.as_completed(futures):
            tx_id, tx_status, msg = future.result()
            target_claim = futures[future]
            result[tx_status.name].append(tx_id)
            target_claim.tx_status = tx_status
            # if msg:
            #     claim.msg = "\n".join([claim.msg, msg])
            sess.add(target_claim)
    sess.commit()

    logger.info(f"{len(claim_list)} transactions are found to track status: {claim_list[0].id} ~ {claim_list[-1].id}")
    for status, tx_list in result.items():
        if status is None:
            logger.error(f"{len(tx_list)} transactions are not able to track.")
            for tx in tx_list:
                logger.error(tx)
        elif status == TxStatus.STAGED:
            logger.info(f"{len(tx_list)} transactions are still staged.")
        else:
            logger.info(f"{len(tx_list)} transactions are changed to {status}")
