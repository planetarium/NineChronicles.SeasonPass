import concurrent.futures
import json
from collections import defaultdict
from typing import Optional, Tuple

import structlog
from app.config import config
from gql.dsl import DSLQuery, dsl_gql
from shared.enums import PlanetID, TxStatus
from shared.models.user import Claim
from shared.utils._graphql import GQLClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import scoped_session, sessionmaker

logger = structlog.get_logger(__name__)

BLOCK_LIMIT = 200

engine = create_engine(str(config.pg_dsn), pool_size=5, max_overflow=5)


def process(
    planet_id: PlanetID, tx_id: str
) -> Tuple[str, Optional[TxStatus], Optional[str]]:
    client = GQLClient(
        config.converted_gql_url_map,
        config.headless_jwt_secret,
        timeout=config.gql_timeout,
    )
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

    # Read the unsettled claims in a short transaction, then release the
    # connection *before* the per-tx headless calls below. Previously this one
    # session stayed open across the whole GQL batch, so when a node was slow it
    # sat idle-in-transaction for minutes (observed 448s). That pinned the xmin
    # horizon and blocked autovacuum on claim/user_season_pass (dead tuples piled
    # up). Capture just the scalars we need, then close the session so the RPC
    # batch runs untethered to any DB transaction.
    read_sess = scoped_session(sessionmaker(bind=engine))
    try:
        claim_list = read_sess.scalars(
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
        targets = [
            (claim.id, PlanetID(claim.planet_id), claim.tx_id) for claim in claim_list
        ]
    finally:
        read_sess.close()

    if not targets:
        logger.info("No transactions to track", tracker="tx_tracker")
        return

    # Query headless for each tx with no DB transaction held open.
    result = defaultdict(list)
    updates = []  # (claim_id, tx_id, new_status)
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for claim_id, planet_id, tx_id in targets:
            futures[executor.submit(process, planet_id, tx_id)] = claim_id

        for future in concurrent.futures.as_completed(futures):
            tx_id, tx_status, msg = future.result()
            claim_id = futures[future]
            result[tx_status.name].append(tx_id)
            updates.append((claim_id, tx_id, tx_status))
            # if msg:
            #     claim.msg = "\n".join([claim.msg, msg])

    logger.info(
        "Transactions found to track",
        tracker="tx_tracker",
        count=len(targets),
        start_id=targets[0][0],
        end_id=targets[-1][0],
    )

    # Persist the resolved statuses in a fresh, short write transaction. The
    # read->write gap is wider now that the GQL batch runs outside the
    # transaction, so guard the write: match on tx_id (skip a claim re-staged
    # with a new tx in between) AND require the row still be STAGED/INVALID (skip
    # one already finalized to SUCCESS/FAILURE by another path), so we never
    # clobber a settled status with a stale GQL result.
    write_sess = scoped_session(sessionmaker(bind=engine))
    try:
        for claim_id, tx_id, tx_status in updates:
            write_sess.execute(
                update(Claim)
                .where(
                    Claim.id == claim_id,
                    Claim.tx_id == tx_id,
                    Claim.tx_status.in_((TxStatus.STAGED, TxStatus.INVALID)),
                )
                .values(tx_status=tx_status)
            )
        write_sess.commit()
    finally:
        write_sess.close()

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
