import concurrent.futures
import json
import os
from collections import defaultdict
from typing import Optional, Tuple

import structlog
from app.config import config
from gql.dsl import DSLQuery, dsl_gql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import scoped_session, sessionmaker

from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim
from shared.utils._graphql import GQLClient

logger = structlog.get_logger(__name__)

BLOCK_LIMIT = 200

engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def process(
    planet_id: PlanetID, tx_id: str
) -> Tuple[str, Optional[TxStatus], Optional[str]]:
    client = GQLClient(config.gql_url, config.headless_jwt_secret)
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
        logger.error("GQL failed to get transaction status", errors=resp["errors"])
        return tx_id, TxStatus.INVALID, json.dumps(resp["errors"])

    try:
        return (
            tx_id,
            TxStatus[resp["transaction"]["transactionResult"]["txStatus"]],
            json.dumps(resp["transaction"]["transactionResult"]["exceptionNames"]),
        )
    except:
        return (
            tx_id,
            TxStatus.INVALID,
            json.dumps(resp["transaction"]["transactionResult"]["exceptionNames"]),
        )


def track_tx():
    logger.info("Tracking unfinished transactions")
    sess = scoped_session(sessionmaker(bind=engine))
    claim_list = sess.scalars(
        select(Claim)
        .where(
            Claim.tx_status.in_(
                (
                    TxStatus.STAGED,
                    TxStatus.INVALID,
                )
            )
        )
        .order_by(Claim.id)
        .limit(BLOCK_LIMIT)
    ).fetchall()
    result = defaultdict(list)

    if not claim_list:
        logger.info("No transactions to track", tracker="tx_tracker")
        return

    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for claim in claim_list:
            futures[executor.submit(process, PlanetID(claim.planet_id), claim.tx_id)] = (
                claim
            )

        for future in concurrent.futures.as_completed(futures):
            tx_id, tx_status, msg = future.result()
            target_claim = futures[future]
            result[tx_status.name].append(tx_id)
            target_claim.tx_status = tx_status
            # if msg:
            #     claim.msg = "\n".join([claim.msg, msg])
            sess.add(target_claim)
    sess.commit()
    sess.close()

    logger.info(
        "Transactions found to track",
        tracker="tx_tracker",
        count=len(claim_list),
        start_id=claim_list[0].id,
        end_id=claim_list[-1].id,
    )
    for status, tx_list in result.items():
        if status is None:
            logger.error(
                "Transactions not able to track",
                tracker="tx_tracker",
                count=len(tx_list),
            )
            for tx in tx_list:
                logger.error(tx)
        elif status == TxStatus.STAGED:
            logger.info(f"{len(tx_list)} transactions are still staged.")
        else:
            logger.info(f"{len(tx_list)} transactions are changed to {status}")
